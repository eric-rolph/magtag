# SPDX-License-Identifier: MIT
"""Standalone weather station: recover, record, estimate, alert and display."""

import gc
import json
import time
import weather_config as config
from weather_alerts import AlertManager
from weather_boot import editing_mode
from weather_core import DataHistory, SamplingSchedule, ZambrettiForecaster
from weather_hardware import LEDManager, SensorData
from weather_metrics import dew_point_c, nowcast, pm_average, pressure_change
from weather_persistence import CheckpointStore
from weather_runtime import (
    WatchdogGuard,
    age_text,
    free_memory,
    latest_slot_epoch,
    reset_reason,
    trusted_epoch,
)
from weather_settings import validate_config
from weather_ui import UIController, status_messages


class WeatherStation:
    def __init__(self, settings=config):
        errors = validate_config(settings)
        if errors:
            raise ValueError("\n".join(errors))
        import board
        import busio
        from adafruit_magtag.magtag import MagTag
        from weather_display import Display

        self.config = settings
        self.started_at = time.monotonic()
        self.editing = editing_mode()
        self.magtag = MagTag()
        self.i2c = busio.I2C(board.SCL, board.SDA, frequency=100_000)
        self.sensors = SensorData(self.i2c, settings)
        self.forecaster = ZambrettiForecaster()
        self.controls = UIController(debounce_seconds=settings.BUTTON_DEBOUNCE_SECONDS)
        self.display = Display(self.magtag.graphics.display, settings)
        self.leds = LEDManager(
            self.magtag.peripherals, settings.LED_BRIGHTNESS_LEVELS, settings.LED_BRIGHTNESS_INDEX
        )
        self.history = DataHistory(settings.HISTORY_SAMPLES, settings.READING_INTERVAL_SECONDS)
        self.store = CheckpointStore(
            settings.CHECKPOINT_DIRECTORY,
            settings.HISTORY_SAMPLES,
            settings.READING_INTERVAL_SECONDS,
        )
        self.archive = None
        self.restore_info = None
        now = time.monotonic()
        self.schedule = SamplingSchedule(now, settings.READING_INTERVAL_SECONDS)
        epoch = trusted_epoch()
        if settings.PERSISTENCE_ENABLED:
            self.restore_info = self.store.load(self.history, epoch, now)
            if self.restore_info:
                self._apply_preferences(self.restore_info.get("preferences", {}))
            if self.restore_info and not self.restore_info.get("clock_trusted"):
                archive = DataHistory(settings.HISTORY_SAMPLES, settings.READING_INTERVAL_SECONDS)
                archived = self.store.load_archive(archive)
                if archived and archived.get("restored_samples"):
                    self.archive = archive
        if self.history._origin is None:
            self.history._origin = now
        self.alerts = AlertManager(settings)
        self.trend = "-"
        self.forecast = "Outlook: waiting for 3h"
        self.reading_count = 0
        self.last_sample_at = None
        self.dew_point = self.pm_average = self.pressure_delta = None
        self.nowcast_result = nowcast(self.history, None)
        self._preferences_dirty = False
        self._preferences_due = now + 30
        self._checkpoint_due = now + 30
        self._diagnostics_due = now
        self.reset_cause = reset_reason()
        self.watchdog = WatchdogGuard(
            settings.WATCHDOG_ENABLED, settings.WATCHDOG_TIMEOUT_SECONDS, self.editing
        )
        self.store.progress_callback = self.watchdog.feed
        print(
            "MagTag weather station v4.0; standalone; history payload:",
            self.history.storage_bytes,
            "bytes; watchdog",
            self.watchdog.status,
        )

    def _preferences(self):
        return {
            "graph_index": self.display.graph_index,
            "resolution_index": self.display.resolution_index,
            "brightness_index": self.leds.brightness_index,
            "temperature_unit": self.display.temperature_unit,
        }

    def _apply_preferences(self, preferences):
        graph = preferences.get("graph_index")
        resolution = preferences.get("resolution_index")
        brightness = preferences.get("brightness_index")
        unit = preferences.get("temperature_unit")
        if isinstance(graph, int) and 0 <= graph < 4:
            self.display.graph_index = graph
        if isinstance(resolution, int) and 0 <= resolution < len(self.config.RESOLUTION_HOURS):
            self.display.resolution_index = resolution
        if isinstance(brightness, int) and 0 <= brightness < len(self.leds.levels):
            self.leds.brightness_index = brightness
            self.leds._apply_brightness()
        if unit in ("F", "C"):
            self.display.temperature_unit = unit

    def _changed_preferences(self, now):
        if not self._preferences_dirty:
            self._preferences_due = now + 30
        self._preferences_dirty = True

    def _update_sensor_labels(self):
        self.display.update_sensors(
            self.sensors, self.trend, self.forecast, self.magtag.peripherals.battery
        )

    def check_buttons(self, now):
        peripherals = self.magtag.peripherals
        pressed = (
            peripherals.button_a_pressed,
            peripherals.button_b_pressed,
            peripherals.button_c_pressed,
            peripherals.button_d_pressed,
        )
        for action in self.controls.update(pressed, now):
            if action == "metric":
                self.display.next_graph_type()
                message = self.display.DATA_TYPES[self.display.graph_index] + " graph"
            elif action == "range":
                self.display.cycle_resolution()
                hours = self.config.RESOLUTION_HOURS[self.display.resolution_index]
                message = "Graph range: {:g}h".format(hours)
            elif action == "page":
                self.display.next_screen(self.archive is not None)
                message = self.display.screen.capitalize()
            elif action == "home":
                self.display.home()
                message = "Home dashboard"
            elif action == "units":
                self.display.toggle_temperature_unit()
                message = "Temperature: " + self.display.temperature_unit
            else:
                self.leds.cycle_brightness()
                brightness = self.leds.levels[self.leds.brightness_index]
                message = (
                    "LEDs off"
                    if brightness == 0
                    else "LED brightness: {:.0f}%".format(brightness * 100)
                )
            if action in ("metric", "range", "units", "brightness"):
                self._changed_preferences(now)
            self._update_sensor_labels()
            self._update_details(now)
            self.display.acknowledge(message, now)
            self._diagnostics_due = now

    def _update_context(self, now):
        self.display.set_context(
            now, self.sensors.health(now), max(0, now - self.started_at), self.archive is not None
        )

    def _update_details(self, now):
        self._update_context(now)
        self.display.update_status(
            status_messages(
                self.history,
                self.sensors,
                self.nowcast_result,
                self.config,
                self.pm_average,
                self.pressure_delta,
                self.dew_point,
            )
        )
        self.display.update_details(
            self.dew_point,
            self.pm_average,
            self.pressure_delta,
            self.nowcast_result,
            self.alerts.text,
            trusted_epoch(),
            self.archive is not None,
        )

    def update_diagnostics(self, now):
        if now < self._diagnostics_due:
            return
        self._diagnostics_due = now + self.config.DIAGNOSTIC_REFRESH_SECONDS
        self.display.update_diagnostics(
            {
                "uptime": int(now - self.started_at),
                "samples": self.reading_count,
                "health": self.sensors.health(now),
                "memory": free_memory(),
                "reset": self.reset_cause,
                "storage": "paused: editing" if self.editing else self.store.status,
                "clock": "UTC" if trusted_epoch() is not None else "session hours",
                "watchdog": self.watchdog.status,
                "sample_age": age_text(self.last_sample_at, now),
            },
            now,
        )

    def update_readings(self, now):
        if not self.schedule.due(now):
            return False
        self.sensors.read_sensors(now)
        self.history.add_reading(
            self.sensors.pm,
            self.sensors.pressure,
            self.sensors.temperature_f,
            self.sensors.humidity,
            now,
        )
        self.schedule.advance(time.monotonic())
        self.trend = self.history.pressure_trend(
            self.config.TREND_WINDOW_SECONDS,
            self.config.TREND_THRESHOLD_HPA,
            self.config.TREND_MAX_GAP_SECONDS,
        )
        self.forecast = self.forecaster.get_forecast(self.sensors.pressure, self.trend)
        self.dew_point = dew_point_c(self.sensors.temperature_c, self.sensors.humidity)
        self.pm_average = pm_average(self.history, self.config.PM_AVERAGE_SECONDS)
        self.pressure_delta = pressure_change(
            self.history, self.config.TREND_WINDOW_SECONDS, self.config.TREND_MAX_GAP_SECONDS
        )
        epoch = trusted_epoch()
        if epoch is not None:
            slot_epoch = latest_slot_epoch(self.history, time.monotonic(), epoch)
            basis = "utc"
        else:
            slot_epoch = (
                self.history._origin + self.history._last_slot * self.history.interval_seconds
            )
            slot_epoch -= self.started_at
            basis = "session"
        self.nowcast_result = nowcast(
            self.history, slot_epoch, self.config.HOURLY_MINIMUM_COVERAGE, time_basis=basis
        )
        self.alerts.update(self.pm_average, self.pressure_delta, now)
        self._update_sensor_labels()
        self._update_details(now)
        self.leds.set_color(self.sensors.get_air_quality_category()[1])
        self.display.graph_dirty = True
        self.reading_count += 1
        self.last_sample_at = now
        self._diagnostics_due = now
        gc.collect()
        print(
            "SAMPLE_JSON",
            json.dumps(
                {
                    "epoch": epoch,
                    "monotonic": now,
                    "pm25": self.sensors.pm,
                    "pressure_hpa": self.sensors.pressure,
                    "temperature_c": self.sensors.temperature_c,
                    "humidity": self.sensors.humidity,
                    "aqi_estimate": self.nowcast_result["aqi"],
                    "time_basis": basis,
                    "alerts": self.alerts.active_names,
                }
            ),
        )
        return True

    def checkpoint(self, now):
        if not self.config.PERSISTENCE_ENABLED or self.editing:
            return
        if self.store.saving:
            finished = self.store.service_save()
            if finished is not None:
                self._diagnostics_due = now
            return
        if self._preferences_dirty and now >= self._preferences_due:
            if self.store.save_preferences(self._preferences()):
                self._preferences_dirty = False
            self._preferences_due = now + 30
        if now >= self._checkpoint_due:
            self._checkpoint_due = now + self.config.CHECKPOINT_INTERVAL_SECONDS
            self.store.start_save(
                self.history, self._preferences(), trusted_epoch(), now_monotonic=now
            )
            self._diagnostics_due = now

    def tick(self, now):
        self.check_buttons(now)
        self.sensors.service_retries(now)
        self.update_readings(now)
        self.update_diagnostics(now)
        self.display.update_graph(
            self.archive if self.display.screen == "archive" else self.history
        )
        if self.display.refresh(time.monotonic()):
            self.controls.rendered()
        self.checkpoint(time.monotonic())
        self.watchdog.feed()

    def run(self):
        while True:
            self.tick(time.monotonic())
            time.sleep(self.config.POLL_INTERVAL_SECONDS)


def show_startup_error(errors):
    """Keep settings failures readable on serial and the e-paper display."""
    print("SETTINGS ERROR:\n" + "\n".join(errors))
    import board
    import displayio
    import terminalio
    from adafruit_display_text import bitmap_label

    group = displayio.Group()
    palette = displayio.Palette(1)
    palette[0] = 0xFFFFFF
    group.append(
        displayio.TileGrid(
            displayio.Bitmap(board.DISPLAY.width, board.DISPLAY.height, 1), pixel_shader=palette
        )
    )
    lines = ["SETTINGS ERROR", "Edit weather_config.py; hold D at reset"] + errors[:6]
    for index, text in enumerate(lines):
        group.append(
            bitmap_label.Label(terminalio.FONT, text=text[:47], color=0, x=4, y=8 + index * 14)
        )
    if hasattr(board.DISPLAY, "root_group"):
        board.DISPLAY.root_group = group
    else:
        board.DISPLAY.show(group)
    for _ in range(20):
        try:
            board.DISPLAY.refresh()
            break
        except RuntimeError:
            time.sleep(1)


station = None


def main():
    global station
    errors = validate_config(config)
    if errors:
        show_startup_error(errors)
        return
    station = WeatherStation()
    try:
        station.run()
    except KeyboardInterrupt:
        station.store.cancel_save()
        station.watchdog.stop()
        raise
