# SPDX-License-Identifier: MIT
"""Exercise the real CircuitPython display tree using a temporary test station.

Call run(station) with persistence and watchdog disabled for the test session.
Does not write files or refresh the panel; the caller restores normal operation.
"""

import gc
import time
import displayio
import terminalio
from adafruit_display_text.bitmap_label import Label
from weather_core import DataHistory


def rectangles(group, x=0, y=0, scale=1, cache=None):
    if cache is None:
        cache = {}
    if group.hidden:
        return []
    x, y = x + group.x * scale, y + group.y * scale
    scale *= group.scale
    result = []
    for item in group:
        if getattr(item, "hidden", False):
            continue
        if isinstance(item, Label) and item.text:
            bx, by, _, _ = item.bounding_box
            bitmap, palette = item._bitmap, item._tilegrid.pixel_shader
            # Terminal glyphs have transparent padding inside their 14px cells.
            # Measure visible pixels, not the overlapping empty cell margins.
            key = (id(item.font), item.text)
            if key not in cache:
                left_ink, top_ink, right_ink, bottom_ink = bitmap.width, bitmap.height, 0, 0
                for py in range(bitmap.height):
                    for px in range(bitmap.width):
                        if not palette.is_transparent(bitmap[px, py]):
                            left_ink, top_ink = min(left_ink, px), min(top_ink, py)
                            right_ink = max(right_ink, px + 1)
                            bottom_ink = max(bottom_ink, py + 1)
                cache[key] = (left_ink, top_ink, right_ink, bottom_ink)
            left_ink, top_ink, right_ink, bottom_ink = cache[key]
            if right_ink == 0:
                continue
            sx = scale * item.scale
            left, top = x + item.x * scale + bx * sx, y + item.y * scale + by * sx
            result.append((item.text, (left + left_ink * sx, top + top_ink * sx,
                                      left + right_ink * sx, top + bottom_ink * sx)))
        elif isinstance(item, displayio.Group):
            result.extend(rectangles(item, x, y, scale, cache))
    return result


def run(station):
    view = station.display
    report = {"cases": 0, "failures": [], "fonts_loaded": view.large_font is not terminalio.FONT}
    ink_cache = {}

    def check_layout(name):
        rects = rectangles(view.root, cache=ink_cache)
        report["cases"] += 1
        for index, (text, a) in enumerate(rects):
            if not (0 <= a[0] < a[2] <= 296 and 0 <= a[1] < a[3] <= 128):
                report["failures"].append((name, "bounds", text, a))
            for other, b in rects[index + 1:]:
                if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                    report["failures"].append((name, "overlap", text, other, a, b))

    class Readings:
        temperature_f = 72.4
        pm = 12.0
        pressure = 1012.8
        humidity = 43

    demo = Readings()
    history = DataHistory(720)
    for slot in range(720):
        missing = 250 <= slot < 290
        history.add_reading(None if missing else (200 if slot == 400 else 12), 1012,
                            None if missing else 70 + slot / 100.0, 43, slot * 60)
    health = ({"state": "ready", "last_good": 43200}, {"state": "ready", "last_good": 43200})
    view.set_context(43208, health, 43208, True)
    for unit in ("F", "C"):
        view.temperature_unit = unit
        for temp, pm in ((72.4, 12.0), (72.4, 60.2), (72.4, 80.0),
                         (-40, 3276.7), (185, 225.5), (None, None)):
            demo.temperature_f, demo.pm = temp, pm
            view.update_sensors(demo, "rising", "Outlook: Fair, improving", 4.2)
            view.update_details(9.2 if temp is not None else None, pm, 2.1,
                                {"aqi": 9999, "category": "V.Unhealthy"}, "Alerts: clear", None)
            view.home()
            view.update_graph(history)
            check_layout(unit + ":" + str(temp) + ":" + str(pm))
    demo.temperature_f, demo.pm = 72.4, 60.2
    view.update_sensors(demo, "rising", "Outlook: Fair, improving", 4.2)
    for alert in ("Alerts: clear", "Alert: PM high, Pressure change"):
        view.update_details(9.2, 48, 2.1, {"aqi": 110, "category": "Sensitive"}, alert, None, True)
        for screen in ("home", "weather", "details", "diagnostics", "archive"):
            view.screen = screen
            view._show_screen()
            view.update_graph(history)
            check_layout(screen + ":" + alert)
    view.home()
    gc.collect()
    before = gc.mem_free()
    started = time.monotonic()
    for _ in range(40):
        view.toggle_temperature_unit()
        view.update_graph(history)
    gc.collect()
    report["redraws"] = 40
    report["redraw_seconds"] = time.monotonic() - started
    report["free_before"] = before
    report["free_after"] = gc.mem_free()
    report["numeric_bounds"] = {name: getattr(view, name).bounding_box
                                for name in ("temperature", "pm", "humidity", "dew", "pressure")}
    report["passed"] = report["fonts_loaded"] and not report["failures"]
    return report
