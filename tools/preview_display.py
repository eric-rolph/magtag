"""Render actual v4 display trees with synthetic readings and desktop fonts."""

import argparse
import math
from pathlib import Path
import sys
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from tools.display_stubs import FakeDisplay, Group, Label, TileGrid, install  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def visible_items(group, offset_x=0, offset_y=0, scale=1):
    """Yield visible leaves with inherited group offsets and scale."""
    if getattr(group, "hidden", False):
        return
    offset_x += getattr(group, "x", 0) * scale
    offset_y += getattr(group, "y", 0) * scale
    scale *= getattr(group, "scale", 1)
    for item in group:
        if getattr(item, "hidden", False):
            continue
        if isinstance(item, Group):
            yield from visible_items(item, offset_x, offset_y, scale)
        else:
            yield item, offset_x, offset_y, scale


def rgb(value):
    """Convert a CircuitPython RGB integer into a Pillow color."""
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


def desktop_font():
    """Find a compact monospace font without requiring a Windows installation."""
    for name in ("C:/Windows/Fonts/cour.ttf", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf"):
        try:
            return ImageFont.truetype(name, 10)
        except OSError:
            pass
    return ImageFont.load_default()


def render_screen(view, font):
    """Render the actual visible tree, omitting hidden page groups and labels."""
    screen = Image.new("RGB", (view.display.width, view.display.height), "white")
    draw = ImageDraw.Draw(screen)
    for item, offset_x, offset_y, scale in visible_items(view.root):
        x = offset_x + getattr(item, "x", 0) * scale
        y = offset_y + getattr(item, "y", 0) * scale
        if isinstance(item, TileGrid):
            for bitmap_y in range(item.bitmap.height):
                for bitmap_x in range(item.bitmap.width):
                    pixel = item.bitmap.pixels[bitmap_y * item.bitmap.width + bitmap_x]
                    if pixel in getattr(item.pixel_shader, "transparent", ()):
                        continue
                    left, top = x + bitmap_x * scale, y + bitmap_y * scale
                    draw.rectangle(
                        (left, top, left + scale - 1, top + scale - 1),
                        fill=rgb(item.pixel_shader[pixel]),
                    )
        elif isinstance(item, Label) and item.text:
            mask = Image.new("RGBA", (max(1, len(item.text) * 6), 12), (255, 255, 255, 0))
            ImageDraw.Draw(mask).text(
                (0, -1), item.text, fill=rgb(getattr(item, "color", 0)), font=font
            )
            label_scale = scale * item.scale
            if label_scale > 1:
                mask = mask.resize(
                    (mask.width * label_scale, mask.height * label_scale),
                    resample=Image.Resampling.NEAREST,
                )
            screen.paste(mask, (x, y - mask.height // 2), mask)
    return screen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="display-preview.png")
    arguments = parser.parse_args(argv)
    install()
    import weather_config as config
    from weather_core import DataHistory, ZambrettiForecaster, pm_category
    from weather_display import Display
    from weather_metrics import dew_point_c, nowcast, pm_average, pressure_change
    from weather_ui import status_messages

    history, archive, warmup, offline = (DataHistory() for _ in range(4))
    for index in range(720):
        pm = 5 + 3 * math.sin(index / 40) + (40 if 400 <= index <= 404 else 0)
        values = (
            pm,
            1010 + index / 180,
            68 + 4 * math.sin(index / 150),
            40 + 10 * math.sin(index / 100),
        )
        # A deliberate outage makes the graph's missing-data gap inspectable.
        history.add_reading(*(values if not 250 <= index < 290 else (None,) * 4), index * 60)
        offline.add_reading(*(values if not 250 <= index < 290 else (None,) * 4), index * 60)
        archive.add_reading(
            12 + 6 * math.sin(index / 30),
            1008 + index / 200,
            72 + 3 * math.sin(index / 150),
            48 + 8 * math.cos(index / 100),
            index * 60,
        )
    for index in range(3):
        warmup.add_reading(7.0, 1013.2, 70.0, 42.0, index * 60)
    for index in range(720, 725):
        offline.add_reading(
            None, history.value_ago(1), history.value_ago(2), history.value_ago(3), index * 60
        )

    def sensors_for(source, pm=None, missing_pm=False):
        concentration = source.value_ago(0) if pm is None else pm
        if missing_pm:
            concentration = None
        return SimpleNamespace(
            pm=concentration,
            pressure=source.value_ago(1),
            temperature_f=source.value_ago(2),
            temperature_c=(source.value_ago(2) - 32) / 1.8,
            humidity=source.value_ago(3),
            get_air_quality_category=lambda: pm_category(concentration),
        )

    current_time = 719 * 60
    health = (
        {"state": "ready", "errors": 2, "last_good": current_time},
        {"state": "ready", "errors": 1, "last_good": current_time},
    )
    font = desktop_font()
    sheet = Image.new("RGB", (632, 1058), "#e9edf2")
    outer = ImageDraw.Draw(sheet)
    outer.text((12, 8), "MAGTAG v4 | SYNTHETIC LAYOUT PREVIEW", fill="#243244", font=font)
    outer.text(
        (12, 23),
        "Desktop font approximation; not a device photo or hardware test.",
        fill="#526174",
        font=font,
    )
    screens = (
        ("Home: large current readings", "home", 0, "normal"),
        ("PM2.5: missing interval and retained peak", "weather", 0, "normal"),
        ("Pressure: dedicated trend graph", "weather", 1, "normal"),
        ("Temperature: graph in Celsius", "weather", 2, "normal"),
        ("Humidity: dedicated graph", "weather", 3, "normal"),
        ("Measurements: session-hour estimate", "details", 0, "normal"),
        ("Diagnostics: illustrative device status", "diagnostics", 0, "normal"),
        ("Archive: separated previous session", "archive", 0, "normal"),
        ("Alert: visible with LEDs switched off", "home", 0, "alert"),
        ("PM unavailable: recovery and reading age", "home", 0, "offline"),
        ("Startup: explained history warmup", "details", 0, "warmup"),
        ("Feedback: brightness change acknowledged", "home", 0, "feedback"),
    )
    for index, (title, screen_name, graph_index, state) in enumerate(screens):
        view = Display(FakeDisplay(), config)
        view.graph_index = graph_index
        if graph_index == 2 or screen_name == "details":
            view.temperature_unit = "C"
        view.screen = screen_name
        view._show_screen()
        source = warmup if state == "warmup" else (offline if state == "offline" else history)
        now = 120 if state == "warmup" else current_time + (300 if state == "offline" else 0)
        sensors = sensors_for(
            source, pm=80.0 if state == "alert" else None, missing_pm=state == "offline"
        )
        sample_health = tuple(dict(item) for item in health)
        if state == "offline":
            sample_health[0].update(state="retrying", last_good=now - 300, errors=12)
            sample_health[1]["last_good"] = now
        elif state == "warmup":
            for item in sample_health:
                item["last_good"] = now
        view.set_context(now, sample_health, now, has_archive=True)
        trend = source.pressure_trend()
        forecast = ZambrettiForecaster().get_forecast(sensors.pressure, trend)
        estimate = nowcast(source, now, time_basis="session")
        view.update_sensors(sensors, trend, forecast, 4.1)
        dew = dew_point_c(sensors.temperature_c, sensors.humidity)
        pm_mean, pressure_delta = pm_average(source), pressure_change(source)
        view.update_details(
            dew,
            pm_mean,
            pressure_delta,
            estimate,
            "Alert: PM high" if state == "alert" else "Alerts: clear",
            None,
            has_archive=True,
        )
        status = status_messages(source, sensors, estimate, config, pm_mean, pressure_delta, dew)
        status["sample_age"] = "0s"
        view.update_status(status)
        view.update_diagnostics(
            {
                "health": sample_health,
                "memory": 2048 * 1024,
                "uptime": now,
                "samples": source.data_count,
                "reset": "POWER_ON",
                "storage": "saved; device logging",
                "clock": "session (RTC unset)",
                "watchdog": "enabled 30s",
                "sample_age": "0s",
            },
            now,
        )
        view.update_graph(archive if screen_name == "archive" else source)
        if state == "feedback":
            view.acknowledge("LED brightness: off", now)
        x, y = 12 + (index % 2) * 312, 58 + (index // 2) * 160
        outer.text((x, y - 17), title, fill="#243244", font=font)
        sheet.paste(render_screen(view, font), (x, y))
        outer.rectangle((x - 1, y - 1, x + 296, y + 128), outline="#b1bac5")

    outer.text(
        (12, 1005),
        "296 x 128 pixels per panel | shown at 2x | 12 illustrated states",
        fill="#526174",
        font=font,
    )
    outer.text(
        (12, 1020),
        "Actual display tree, visibility and palette; all values are synthetic.",
        fill="#526174",
        font=font,
    )
    outer.text(
        (12, 1035),
        "Desktop font approximation; visual preview does not establish hardware performance.",
        fill="#526174",
        font=font,
    )
    output = Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.resize((1264, 2116), Image.Resampling.NEAREST).save(output)
    print("Saved", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
