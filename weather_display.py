# SPDX-License-Identifier: MIT
"""Glanceable monochrome pages and bounded, deferred e-paper rendering."""

import displayio
import terminalio
import time
from adafruit_display_text import bitmap_label
from weather_metrics import temperature_from_f
from weather_runtime import age_text


class _NumberFont:
    """Use actual numeric glyph extents; source BDF ascents clip some digits."""

    def __init__(self, font):
        self.source_font = font
        glyphs = [font.get_glyph(ord(char)) for char in "0123456789.-%+M"]
        if any(glyph is None for glyph in glyphs):
            raise ValueError("Numeric font is missing required glyphs")
        self.ascent = max(glyph.height + glyph.dy for glyph in glyphs)
        self.descent = max(0, max(-glyph.dy for glyph in glyphs))
        self._bounds = (max(glyph.width + glyph.dx for glyph in glyphs),
                        self.ascent + self.descent, 0, -self.descent)

    def get_glyph(self, code_point):
        return self.source_font.get_glyph(code_point)

    def get_bounding_box(self):
        return self._bounds


class Display:
    DATA_TYPES = ("PM2.5", "Pressure", "Temp", "Humidity")
    UNITS = ("ug/m3", "hPa", "F", "%RH")
    HOME_HOURS = 12

    def __init__(self, display, config):
        self.display = display
        self.config = config
        self.graph_index = 0
        self.resolution_index = config.RESOLUTION_HOURS.index(config.DEFAULT_GRAPH_HOURS)
        self.refresh_needed = True
        self.graph_dirty = True
        self.screen = "home"
        self.temperature_unit = config.TEMPERATURE_UNIT
        self._retry_at = 0.0
        self._refresh_error = False
        self._blocked_since = None
        self._failed_since = None
        self._now = self._uptime = 0
        self._health = ({}, {})
        self._has_archive = False
        self._sensors = None
        self._forecast = "Forecast: collecting 3h"
        self._battery_voltage = None
        self._details = (None, None, None, {}, "Alerts: data pending", None)
        self._status = {}
        self._alert_text = ""
        self._ack_text = None
        self._ack_rendered_at = None
        self._ack_duration = 12
        self.root = displayio.Group()
        self.palette = displayio.Palette(2)
        self.palette[0], self.palette[1] = 0xFFFFFF, 0x000000
        background = displayio.Bitmap(display.width, display.height, 1)
        self.root.append(displayio.TileGrid(background, pixel_shader=self.palette))
        self.home_group = displayio.Group()
        self.weather_group = displayio.Group()
        self.details_group = displayio.Group()
        self.diagnostics_group = displayio.Group()
        for group in (
            self.home_group,
            self.weather_group,
            self.details_group,
            self.diagnostics_group,
        ):
            self.root.append(group)

        self.large_font, self.small_font = self._load_fonts()
        self._label("TEMPERATURE", 8, 22, group=self.home_group)
        self._label("PM2.5", 158, 22, group=self.home_group)
        self.temperature = self._number_label(8, 62, self.large_font)
        self.temperature_units = self._label(self.temperature_unit, 128, 56, group=self.home_group)
        self.pm = self._number_label(158, 62, self.large_font)
        self._label("ug/m3", 254, 21, group=self.home_group)
        self._label("RH", 8, 85, group=self.home_group)
        self.dew_caption = self._label("DEW F", 78, 85, group=self.home_group)
        self._label("hPa", 148, 85, group=self.home_group)
        self._label("EST AQI", 230, 85, group=self.home_group)
        self.humidity = self._number_label(8, 104, self.small_font)
        self.dew = self._number_label(78, 104, self.small_font)
        self.pressure = self._number_label(148, 104, self.small_font)
        self.pm_status = self._number_label(230, 104, self.small_font)
        self.aqi_band = self._label("WAIT", 266, 97, group=self.home_group)
        self.home_header_group = displayio.Group()
        self.home_freshness = self._label("", 110, 7, group=self.home_header_group)
        self.battery = self._label("", 260, 7, group=self.home_header_group)
        for text, x in (("A Metric", 8), ("B Range", 84), ("C LEDs", 160), ("D Page", 254)):
            self._label(text, x, 121, group=self.home_group)
        # Keep the full age descriptions on Measurements, with compact ages at Home.
        self.home_age = self._label("", 4, 95, group=self.home_group)
        self.home_age.hidden = True
        rules = displayio.Bitmap(display.width, display.height, 2)
        for y in (12, 79, 116):
            for x in range(6, display.width - 6):
                rules[x, y] = 1
        for y in range(16, 78):
            rules[148, y] = 1
        rule_palette = displayio.Palette(2)
        rule_palette[1] = 0x000000
        rule_palette.make_transparent(0)
        self.home_group.append(displayio.TileGrid(rules, pixel_shader=rule_palette))
        self.home_sparks = []
        for x in (8, 158):
            spark = displayio.Bitmap(101, 12, 2)
            self.home_sparks.append(spark)
            self.home_group.append(displayio.TileGrid(spark, pixel_shader=self.palette, x=x, y=66))
            self._label("12h", x + 110, 72, group=self.home_group)

        self.graph_x, self.graph_y = 40, 36
        self.graph_width = display.width - self.graph_x - 2
        self.graph_height = 52
        self.bitmap = displayio.Bitmap(self.graph_width, self.graph_height, 2)
        self.weather_group.append(
            displayio.TileGrid(
                self.bitmap, pixel_shader=self.palette, x=self.graph_x, y=self.graph_y
            )
        )
        self.value = self._label("Now --", 4, 23)
        self.unit = self._label("ug/m3", 70, 23)
        self.stats = self._label("Avg --", 110, 23)
        self.coverage = self._label("0/0", 222, 23)
        self.graph_max = self._label("--", 2, self.graph_y + 5)
        self.graph_min = self._label("--", 2, self.graph_y + self.graph_height - 5)
        self.graph_start = self._label("-12h", self.graph_x, 96)
        self.graph_middle = self._label("-6h", 158, 96)
        self.graph_end = self._label("now", 276, 96)
        self.graph_message = self._label("Collecting readings", 66, 60)
        self.details_labels = [
            self._label("", 4, 23 + index * 12, group=self.details_group) for index in range(7)
        ]
        self.diagnostic_labels = [
            self._label("", 4, 23 + index * 12, group=self.diagnostics_group) for index in range(7)
        ]

        # One shared header and footer make controls consistent across pages.
        self.alert_group = displayio.Group()
        banner = displayio.Bitmap(display.width, 15, 2)
        banner.fill(1)
        self.alert_group.append(displayio.TileGrid(banner, pixel_shader=self.palette))
        self.root.append(self.alert_group)
        self.root.append(self.home_header_group)
        self.head = self._label("WEATHER", 4, 8, group=self.root)
        self.page_badge = self._label("1/4", 272, 8, group=self.root)
        self.outlook = self._label("Hold C: F/C  Hold D: Home", 4, 108, group=self.root)
        self.footer = self._label("A Metric B Range C LEDs D Page", 4, 120, group=self.root)
        self._show_screen()
        # CircuitPython 7 has show(); recent releases expose root_group.
        if hasattr(display, "root_group"):
            display.root_group = self.root
        else:
            display.show(self.root)

    @staticmethod
    def _load_fonts():
        """Bound the glyph cache; missing optional font assets retain a usable UI."""
        try:
            from adafruit_bitmap_font import bitmap_font

            fonts = tuple(
                bitmap_font.load_font("fonts/leaguespartan" + size + ".bdf")
                for size in ("18", "11")
            )
            for font in fonts:
                font.load_glyphs("0123456789.-%+M ")
            return tuple(_NumberFont(font) for font in fonts)
        except (ImportError, OSError, ValueError, RuntimeError, MemoryError,
                AttributeError, TypeError, IndexError) as error:
            print("Instrument fonts unavailable; using built-in font:", error)
            return terminalio.FONT, terminalio.FONT

    def _number_label(self, x, bottom, font):
        item = bitmap_label.Label(
            font, text="--", color=0x000000, scale=1, x=x, y=bottom,
        )
        self.home_group.append(item)
        return item

    @staticmethod
    def _number(item, text, x, bottom, width, preferred_scale=1):
        """Fit actual glyph bounds, retaining decimals and signs at sensor extremes."""
        if item.text != text:
            item.text = text
        scale = preferred_scale
        while scale > 1 and item.bounding_box[2] * scale > width:
            scale -= 1
        item.scale = scale
        # Old bitmap_label anchors use tight glyph height even when the actual
        # bitmap is a full font line. Position by baseline so '--' cannot drop
        # below its row when a sensor becomes unavailable.
        ascent = getattr(item.font, "ascent", None)
        if ascent is None:
            ascent = item.font.get_bounding_box()[1]
        item.x = x - item.bounding_box[0] * scale
        item.y = bottom - (ascent + item.bounding_box[1]) * scale

    def _label(self, text, x, y, scale=1, group=None):
        item = bitmap_label.Label(terminalio.FONT, text=text, color=0x000000, x=x, y=y, scale=scale)
        (self.weather_group if group is None else group).append(item)
        return item

    def _put(self, item, text, maximum=None):
        maximum = (
            maximum if maximum is not None else (self.display.width - item.x) // (6 * item.scale)
        )
        # Device font is ASCII; avoid invisible unicode minus/degree symbols.
        text = str(text).replace("\u00b0", "").replace("\u2212", "-")
        item.text = text[:maximum]

    def _screens(self):
        screens = ("home", "weather", "details", "diagnostics")
        return screens + ("archive",) if self._has_archive else screens

    def next_screen(self, has_archive=False):
        self._has_archive = has_archive
        screens = self._screens()
        current = screens.index(self.screen) if self.screen in screens else 0
        self.screen = screens[(current + 1) % len(screens)]
        self._show_screen()

    def _show_screen(self):
        self.home_group.hidden = self.screen != "home"
        self.home_header_group.hidden = self.screen != "home"
        self.weather_group.hidden = self.screen not in ("weather", "archive")
        self.details_group.hidden = self.screen != "details"
        self.diagnostics_group.hidden = self.screen != "diagnostics"
        self._render_header()
        self._render_home()
        self._render_details()
        self._render_status()
        self.graph_dirty = True
        self.refresh_needed = True

    @staticmethod
    def _duration(hours):
        return "1w" if hours == 168 else ("3d" if hours == 72 else "{:g}h".format(hours))

    def _render_header(self):
        hours = self.config.RESOLUTION_HOURS[self.resolution_index]
        titles = {
            "home": "WEATHER",
            "weather": self.DATA_TYPES[self.graph_index] + " " + self._duration(hours),
            "details": "MEASUREMENTS",
            "diagnostics": "DEVICE STATUS",
            "archive": "PREVIOUS "
            + self.DATA_TYPES[self.graph_index]
            + " "
            + self._duration(hours),
        }
        self.alert_group.hidden = not bool(self._alert_text)
        color = 0xFFFFFF if self._alert_text else 0x000000
        self.head.color = self.page_badge.color = color
        self.home_freshness.color = self.battery.color = color
        title = self._alert_text or titles[self.screen]
        if self._alert_text and self.screen == "archive":
            title = "ARCHIVE | " + title
        at_home = self.screen == "home"
        if at_home and self._alert_text:
            title = "ALERT"
        elif at_home:
            failed = [name for name, index in (("PM", 0), ("BME", 1))
                      if self._health[index].get("state") not in (None, "ready", "initializing")]
            if failed:
                title = "/".join(failed) + " OFFLINE"
        self._put(self.head, title, 17 if at_home else 43)
        self.head.y = 7 if at_home else 8
        self.page_badge.hidden = at_home
        self.footer.hidden = at_home
        self.outlook.y = 109 if at_home else 108
        self.footer.y = 123 if at_home else 120
        screens = self._screens()
        index = screens.index(self.screen) if self.screen in screens else len(screens) - 1
        self.page_badge.text = "{}/{}".format(index + 1, len(screens))

    def toggle_temperature_unit(self):
        self.temperature_unit = "C" if self.temperature_unit == "F" else "F"
        self._render_home()
        self._render_details()
        self.graph_dirty = self.refresh_needed = True

    def next_graph_type(self):
        if self.screen in ("weather", "archive"):
            self.graph_index = (self.graph_index + 1) % len(self.DATA_TYPES)
        else:
            self.screen = "weather"
        self._show_screen()

    def cycle_resolution(self):
        if self.screen in ("weather", "archive"):
            self.resolution_index = (self.resolution_index + 1) % len(self.config.RESOLUTION_HOURS)
        else:
            self.screen = "weather"
        self._show_screen()

    def home(self):
        self.screen = "home"
        self._show_screen()

    @staticmethod
    def _format(value, decimals=1):
        if value is None:
            return "--"
        return ("{:.1f}" if decimals else "{:.0f}").format(value)

    def set_context(self, now, health, uptime_seconds, has_archive=False):
        """Cache metadata; elapsed time alone never requests an e-paper flash."""
        self._now, self._health, self._uptime = now, health, uptime_seconds
        self._has_archive = has_archive
        self._render_header()
        self._render_ages()

    def update_status(self, status):
        self._status = status
        self._render_home()
        self._render_details()
        self._render_status()

    def acknowledge(self, text, now, duration_seconds=12):
        """Feedback starts its timeout only after the display accepts a refresh."""
        self._now = now
        self._ack_text = text
        self._ack_duration = duration_seconds
        self._ack_rendered_at = None
        self._render_status()
        self.refresh_needed = True

    def _sensor_caption(self, name, index, full=False):
        health = self._health[index] if self._health and len(self._health) > index else {}
        state = health.get("state", "initializing")
        good = health.get("last_good")
        age = age_text(good, self._now)
        if full:
            return "{}: {}; good {}; errors {}".format(name, state, age, health.get("errors", 0))
        if state == "ready":
            return name + " good " + age
        if good is None:
            return name + (" starting" if state == "initializing" else " offline; never")
        return "{} retry last {}".format(name, age)

    def _render_ages(self):
        self._put(
            self.home_age, self._sensor_caption("BME", 1) + "; " + self._sensor_caption("PM", 0)
        )
        self._put(self.details_labels[5], self.home_age.text)
        parts = []
        for name, index in (("B", 1), ("P", 0)):
            good = self._health[index].get("last_good")
            seconds = max(0, self._now - good) if good is not None else None
            if seconds is None:
                age = "--"
            elif seconds < 60:
                age = "{}s".format(int(seconds))
            elif seconds < 3600:
                age = "{}m".format(int(seconds // 60))
            elif seconds < 86400:
                age = "{}h".format(int(seconds // 3600))
            else:
                age = "{}d".format(min(999, int(seconds // 86400)))
            parts.append(name + ":" + age)
        self._put(self.home_freshness, " ".join(parts), 24)

    def _reason(self, key, fallback):
        return self._status.get(key) or fallback

    def _render_home(self):
        sensors = self._sensors
        temperature = temperature_from_f(
            sensors.temperature_f if sensors else None, self.temperature_unit
        )
        self._number(self.temperature, self._format(temperature), 8, 62, 112, 2)
        self.temperature_units.text = self.temperature_unit
        self._number(self.pm, self._format(sensors.pm if sensors else None), 158, 62, 132, 2)
        humidity = self._format(sensors.humidity if sensors else None, 0)
        self._number(self.humidity, humidity + ("%" if humidity != "--" else ""), 8, 103, 62)
        dew_c = self._details[0]
        dew = (
            None if dew_c is None else (dew_c * 1.8 + 32 if self.temperature_unit == "F" else dew_c)
        )
        self.dew_caption.text = "DEW " + self.temperature_unit
        self._number(self.dew, self._format(dew), 78, 103, 62)
        self._number(self.pressure, self._format(sensors.pressure if sensors else None),
                     148, 103, 76)
        self._put(
            self.battery, self._format(self._battery_voltage) + "V" if self._battery_voltage else ""
        )
        estimate = self._details[3]
        aqi = estimate.get("aqi")
        if not sensors or sensors.pm is None:
            aqi, band = None, (
                "WAIT" if self._health[0].get("state", "initializing") == "initializing" else "OFF"
            )
        elif aqi is None:
            band = "WAIT"
        else:
            band = {"Good": "GOOD", "Moderate": "MOD", "Sensitive": "USG",
                    "Unhealthy": "UNH", "V.Unhealthy": "VUNH", "Hazardous": "HAZ"}.get(
                        estimate.get("category"), "--")
        self._number(self.pm_status, "--" if aqi is None else ("999+" if aqi > 999 else str(aqi)),
                     230, 103, 32)
        self._put(self.aqi_band, band, 4)
        self._render_ages()

    def update_sensors(self, sensors, trend, forecast, battery_voltage=None):
        self._sensors, self._forecast, self._battery_voltage = sensors, forecast, battery_voltage
        self._render_home()
        self._render_status()
        self.refresh_needed = True

    def update_details(
        self, dew_c, pm_mean, pressure_delta, estimate, alerts, epoch, has_archive=False
    ):
        self._details = (dew_c, pm_mean, pressure_delta, estimate, alerts, epoch)
        self._has_archive = has_archive
        self._alert_text = (
            alerts.replace("Alert:", "ALERT:", 1) if alerts.startswith("Alert:") else ""
        )
        self._render_header()
        self._render_home()
        self._render_details()
        self._render_status()
        if self.screen in ("home", "details") or self._alert_text:
            self.refresh_needed = True

    def _render_details(self):
        dew_c, pm_mean, pressure_delta, estimate, _, epoch = self._details
        dew = (
            None if dew_c is None else (dew_c * 1.8 + 32 if self.temperature_unit == "F" else dew_c)
        )
        dew_text = (
            "Dew point: " + self._format(dew) + " " + self.temperature_unit
            if dew is not None
            else self._reason("dew_reason", "Dew point: sensor data unavailable")
        )
        mean_text = (
            "PM {:g}m mean: ".format(self.config.PM_AVERAGE_SECONDS / 60)
            + self._format(pm_mean)
            + " ug/m3"
            if pm_mean is not None
            else self._reason("pm_mean_reason", "PM mean: collecting 5m")
        )
        pressure_text = (
            "Pressure {:g}h change: ".format(self.config.TREND_WINDOW_SECONDS / 3600)
            + self._format(pressure_delta)
            + " hPa"
            if pressure_delta is not None
            else self._reason("pressure_reason", "Pressure: collecting 3h")
        )
        aqi = estimate.get("aqi")
        aqi_text = (
            "Est NowCast AQI: " + str(aqi)
            if aqi is not None
            else self._reason("aqi_reason", "AQI: needs 2 recent hours")
        )
        if epoch is not None:
            clock = time.localtime(epoch)
            clock_text = "UTC {:04d}-{:02d}-{:02d} {:02d}:{:02d}".format(
                clock.tm_year, clock.tm_mon, clock.tm_mday, clock.tm_hour, clock.tm_min
            )
        else:
            clock_text = "Clock: elapsed hours since startup"
        lines = (
            dew_text,
            mean_text,
            pressure_text,
            aqi_text,
            "Band: "
            + estimate.get("category", "Unknown")
            + " / "
            + estimate.get("time_basis", "session"),
            self.home_age.text,
            clock_text,
        )
        for item, text in zip(self.details_labels, lines):
            self._put(item, text)

    def _render_status(self):
        if self._ack_text is not None:
            text = (
                "Saved session | " + self._ack_text if self.screen == "archive" else self._ack_text
            )
        elif self.screen == "archive":
            text = "Saved session; gap duration unknown"
        elif self.screen in ("details", "diagnostics"):
            text = "Hold C: F/C  Hold D: Home"
        elif self.screen == "weather":
            text = "Missing slots are gaps; Hold D: Home"
        elif self.screen == "home" and self._alert_text:
            text = self._alert_text
        elif self._sensors and (self._sensors.pm is None or self._sensors.temperature_f is None):
            text = "-- means sensor unavailable; retrying"
        elif self._details[2] is None:
            text = self._reason("pressure_reason", "Forecast: collecting 3h")
        else:
            text = "P {:+.1f}hPa/3h | ".format(self._details[2]) + self._forecast.replace(
                "Outlook: ", "")
        self._put(self.outlook, text)

    def update_diagnostics(self, diagnostics, now):
        self._now = now
        self._health = diagnostics.get("health", self._health)
        memory = diagnostics.get("memory")
        lines = (
            self._sensor_caption("PM", 0, True),
            self._sensor_caption("BME", 1, True),
            "Uptime {}h {}m; samples {}".format(
                diagnostics["uptime"] // 3600,
                (diagnostics["uptime"] // 60) % 60,
                diagnostics["samples"],
            ),
            "Free {}k; reset {}".format(
                memory // 1024 if memory is not None else "--", diagnostics["reset"]
            ),
            "Storage: " + diagnostics["storage"],
            "Clock: " + diagnostics["clock"],
            "WDT: " + diagnostics["watchdog"] + "; sample attempt " + diagnostics["sample_age"],
        )
        for item, text in zip(self.diagnostic_labels, lines):
            self._put(item, text)
        self._render_ages()
        if self.screen == "diagnostics":
            self.refresh_needed = True

    def update_graph(self, history):
        if not self.graph_dirty or history is None:
            return
        if self.screen == "home":
            self._update_home_trends(history)
            self.graph_dirty = False
            self.refresh_needed = True
            return
        if self.screen not in ("weather", "archive"):
            return
        hours = self.config.RESOLUTION_HOURS[self.resolution_index]
        window = max(1, int(hours * 3600 / history.interval_seconds))
        low, average, high, valid, count, columns = history.summarize(
            self.graph_index, window, self.graph_width
        )
        current = history.value_ago(self.graph_index)
        if self.graph_index == 2:
            low, average, high, current = tuple(
                temperature_from_f(value, self.temperature_unit)
                for value in (low, average, high, current)
            )
            columns = [
                None
                if extrema is None
                else [temperature_from_f(value, self.temperature_unit) for value in extrema]
                for extrema in columns
            ]
        decimals = 0 if self.graph_index == 3 else 1
        prefix = "End " if self.screen == "archive" else "Now "
        self._put(self.value, prefix + self._format(current, decimals), 10)
        self.unit.text = (
            self.temperature_unit if self.graph_index == 2 else self.UNITS[self.graph_index]
        )
        self._put(self.stats, "Avg " + self._format(average, decimals), 17)
        self._put(self.coverage, "{}/{}".format(valid, window), 12)
        self.graph_start.text = "-" + self._duration(hours)
        self.graph_middle.text = "-" + self._duration(hours / 2)
        self.graph_end.text = "end" if self.screen == "archive" else "now"
        self._put(self.graph_message, "No usable readings in this range" if low is None else "", 35)
        self.draw_graph(columns, low, high)
        self._render_header()
        self._render_status()
        self.graph_dirty = False
        self.refresh_needed = True

    def _update_home_trends(self, history):
        """Two fixed-size twelve-hour plots; no scans on ordinary idle ticks."""
        window = max(1, int(self.HOME_HOURS * 3600 / history.interval_seconds))
        for series, bitmap in zip((2, 0), self.home_sparks):
            low, _, high, _, _, columns = history.summarize(series, window, bitmap.width)
            bitmap.fill(0)
            for x in range(0, bitmap.width, 4):
                bitmap[x, bitmap.height - 1] = 1
            if low is None:
                continue
            if high - low < 0.1:
                low -= 0.5
                high += 0.5
            # A min/max mark for each occupied bucket preserves peaks and gaps.
            for x, extrema in enumerate(columns):
                if extrema is None:
                    continue
                top = 9 - int((extrema[1] - low) / (high - low) * 8)
                bottom = 9 - int((extrema[0] - low) / (high - low) * 8)
                for y in range(max(1, top), min(9, bottom) + 1):
                    bitmap[x, y] = 1

    def draw_graph(self, columns, low, high):
        self.bitmap.fill(0)
        if low is None:
            self.graph_min.text = self.graph_max.text = "--"
            return
        if abs(high - low) < 0.1:
            low -= 0.5
            high += 0.5
        self._put(self.graph_min, "{:.0f}".format(low), 5)
        self._put(self.graph_max, "{:.0f}".format(high), 5)

        def y_for(value):
            y = self.graph_height - 1 - int((value - low) / (high - low) * (self.graph_height - 1))
            return max(0, min(self.graph_height - 1, y))

        if self.graph_index == 0:
            for threshold in (9.0, 35.4, 55.4):
                if low <= threshold <= high:
                    y = y_for(threshold)
                    for x in range(0, self.graph_width, 4):
                        self.bitmap[x, y] = 1
        # Per-column min/max retain spikes; missing buckets stay unconnected.
        for x, extrema in enumerate(columns):
            if extrema is not None:
                top, bottom = y_for(extrema[1]), y_for(extrema[0])
                for y in range(top, bottom + 1):
                    self.bitmap[x, y] = 1

    def refresh(self, now):
        if self._ack_rendered_at is not None and now - self._ack_rendered_at >= self._ack_duration:
            self._ack_text = self._ack_rendered_at = None
            self._render_status()
            self.refresh_needed = True
        if not self.refresh_needed or now < self._retry_at:
            return False
        if getattr(self.display, "busy", False):
            if self._blocked_since is None:
                self._blocked_since = now
            if now - self._blocked_since >= 60:
                raise RuntimeError("Display remained busy for 60 seconds")
            return False
        self._blocked_since = None
        if getattr(self.display, "time_to_refresh", 0) > 0:
            return False
        try:
            self.display.refresh()
        except RuntimeError as error:
            if self._failed_since is None:
                self._failed_since = now
            if now - self._failed_since >= 60:
                raise RuntimeError("Display refresh failed for 60 seconds: " + str(error))
            if not self._refresh_error:
                print("Display refresh deferred:", error)
            self._refresh_error = True
            self._retry_at = now + 1
            return False
        self._refresh_error = False
        self._failed_since = None
        self.refresh_needed = False
        if self._ack_text is not None and self._ack_rendered_at is None:
            self._ack_rendered_at = now
        return True
