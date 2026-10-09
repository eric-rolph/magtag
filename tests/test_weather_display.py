import importlib
from types import SimpleNamespace

import pytest
import weather_config as config
from tools.display_stubs import FakeDisplay, Group, Label, TileGrid, install
from tools.preview_display import render_screen, visible_items
from weather_core import DataHistory, pm_category


@pytest.fixture
def view():
    install()
    module = importlib.import_module("weather_display")
    return module.Display(FakeDisplay(), config)


def sensors(pm=12, temperature=68, pressure=1013, humidity=40):
    return SimpleNamespace(
        pm=pm,
        pressure=pressure,
        temperature_f=temperature,
        humidity=humidity,
        get_air_quality_category=lambda: pm_category(pm),
    )


def context(view, now=160, bme_state="ready", pm_state="ready"):
    view.set_context(
        now,
        (
            {"state": pm_state, "errors": 12, "last_good": 100},
            {"state": bme_state, "errors": 1, "last_good": 159},
        ),
        now,
        has_archive=True,
    )


def details(view, **overrides):
    values = {
        "dew_c": 10,
        "pm_mean": 12,
        "pressure_delta": 1,
        "estimate": {"aqi": 55, "category": "Moderate", "time_basis": "session"},
        "alerts": "Alerts: clear",
        "epoch": None,
        "has_archive": True,
    }
    values.update(overrides)
    view.update_details(**values)


def visible_text(view):
    return [
        item.text for item, *_ in visible_items(view.root) if isinstance(item, Label) and item.text
    ]


def label_rects(view):
    rects = []
    for item, offset_x, offset_y, parent_scale in visible_items(view.root):
        x = offset_x + getattr(item, "x", 0) * parent_scale
        y = offset_y + getattr(item, "y", 0) * parent_scale
        if isinstance(item, Label) and item.text:
            scale = parent_scale * item.scale
            rects.append(
                (item.text, (x, y - 6 * scale, x + len(item.text) * 6 * scale, y + 6 * scale))
            )
        elif isinstance(item, TileGrid):
            assert 0 <= x and x + item.bitmap.width * parent_scale <= view.display.width
            assert 0 <= y and y + item.bitmap.height * parent_scale <= view.display.height
    return rects


def assert_layout(view):
    rects = label_rects(view)
    assert len(rects) >= 3  # The check must traverse nested visible page groups.
    for text, (left, top, right, bottom) in rects:
        assert 0 <= left < right <= view.display.width, text
        assert 0 <= top < bottom <= view.display.height, text
    for index, (first, a) in enumerate(rects):
        for second, b in rects[index + 1 :]:
            overlap = min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])
            assert not overlap, (first, second)


def show(view, screen):
    view.screen = screen
    view._show_screen()


def test_home_is_default_and_large_values_have_units_and_sensor_ages(view):
    context(view)
    view.update_sensors(sensors(), "steady", "Outlook: Fine weather", 4.1)
    details(view)
    assert view.screen == "home"
    assert view.temperature.text == "68.0" and view.temperature.scale == 3
    assert view.pm.text == "12.0" and view.pm.scale == 3
    assert view.temperature_units.text == "F"
    assert view.battery.text == "4.1V"
    assert "Est AQI 55 | Moderate" in visible_text(view)
    assert "BME good 1s; PM good 1m" in visible_text(view)
    assert_layout(view)


@pytest.mark.parametrize("temperature,pm", [(-40, 3276.7), (185, 225.5), (None, None)])
def test_extreme_and_missing_dashboard_values_fit_without_label_overlap(view, temperature, pm):
    context(view)
    view.update_sensors(
        sensors(pm=pm, temperature=temperature, pressure=1284.7, humidity=100), "-", "Waiting", 4.2
    )
    details(view, estimate={"aqi": 9999, "category": "V.Unhealthy"})
    assert_layout(view)


def test_refresh_deferral_does_not_recompute_clean_graph(view):
    show(view, "weather")
    history = DataHistory()
    history.add_reading(12, 1013, 70, 40, 0)
    view.update_graph(history)
    original = list(view.bitmap.pixels)
    view.display.time_to_refresh = 5
    assert not view.refresh(0)
    history.summarize = lambda *_: pytest.fail("Clean graph should not be recomputed")
    view.update_graph(history)
    assert view.bitmap.pixels == original
    view.display.time_to_refresh = 0
    assert view.refresh(5)
    assert view.display.refreshes == 1
    assert not view.refresh(6)


def test_failed_refresh_retries_with_backoff(view):
    view.display.fail_refresh = True
    assert not view.refresh(0)
    view.display.fail_refresh = False
    assert not view.refresh(0.5)
    assert view.refresh(1)


def test_busy_display_preserves_feedback_until_render_then_full_duration(view):
    view.acknowledge("LEDs: 25%", 0, duration_seconds=12)
    view.display.time_to_refresh = 15
    assert not view.refresh(20)
    assert view.outlook.text == "LEDs: 25%"
    view.display.time_to_refresh = 0
    assert view.refresh(21)
    assert not view.refresh(32)
    assert view.outlook.text == "LEDs: 25%"
    assert view.refresh(33)
    assert view.outlook.text != "LEDs: 25%"
    assert not view.refresh(34)


def test_failed_refresh_and_new_sample_never_discard_pending_feedback(view):
    view.acknowledge("Units: Celsius", 0)
    view.display.fail_refresh = True
    assert not view.refresh(1)
    view.update_sensors(sensors(), "steady", "Fine")
    details(view)
    assert view.outlook.text == "Units: Celsius"
    view.display.fail_refresh = False
    assert view.refresh(15)
    assert not view.refresh(26)
    assert view.refresh(27)


def test_new_feedback_replaces_old_feedback_and_gets_own_render_timeout(view):
    view.acknowledge("Units: Celsius", 0)
    assert view.refresh(0)
    view.acknowledge("LEDs: off", 5)
    view.display.busy = True
    assert not view.refresh(50)
    assert view.outlook.text == "LEDs: off"
    view.display.busy = False
    assert view.refresh(51)
    assert not view.refresh(62)


def test_sensor_age_tracks_last_success_instead_of_latest_failed_attempt(view):
    context(view, now=760, pm_state="read failed")
    view.update_sensors(sensors(pm=None), "-", "Waiting")
    assert view.pm.text == "--"
    assert "PM retry last 11m" in view.home_age.text
    assert "sensor unavailable" in view.outlook.text
    assert_layout(view)


def test_elapsed_time_alone_does_not_request_another_refresh(view):
    context(view)
    view.refresh(160)
    context(view, now=161)
    assert not view.refresh_needed
    assert not view.refresh(161)


def test_pending_metrics_use_readable_reasons_on_home_and_measurements(view):
    view.update_status(
        {
            "aqi_reason": "AQI: needs 2 recent hours",
            "pressure_reason": "Pressure: collecting 3h",
            "pm_mean_reason": "PM mean: needs 4 usable samples",
            "dew_reason": "Dew point: humidity unavailable",
        }
    )
    details(view, dew_c=None, pm_mean=None, pressure_delta=None, estimate={"aqi": None})
    assert "AQI: needs 2 recent hours" in visible_text(view)
    show(view, "details")
    text = visible_text(view)
    for line in (
        "Dew point: humidity unavailable",
        "PM mean: needs 4 usable samples",
        "Pressure: collecting 3h",
        "AQI: needs 2 recent hours",
    ):
        assert line in text
    assert "Clock: elapsed hours since startup" in text
    assert_layout(view)


def test_black_and_white_alert_banner_is_prominent_on_every_page(view):
    context(view)
    details(view, alerts="Alert: PM high, Pressure change")
    for page in view._screens():
        show(view, page)
        expected = "ALERT: PM high, Pressure change"
        if page == "archive":
            expected = "ARCHIVE | " + expected
        assert view.head.text == expected
        assert view.head.color == view.page_badge.color == 0xFFFFFF
        assert not view.alert_group.hidden
        assert_layout(view)


def test_alert_clearing_restores_page_title_and_normal_palette(view):
    details(view, alerts="Alert: PM high")
    show(view, "details")
    details(view, alerts="Alerts: clear")
    assert view.head.text == "MEASUREMENTS"
    assert view.head.color == 0x000000
    assert view.alert_group.hidden


def test_banner_renderer_uses_black_palette_and_white_letters(view):
    from PIL import ImageFont

    details(view, alerts="Alert: PM high")
    image = render_screen(view, ImageFont.truetype("C:/Windows/Fonts/cour.ttf", 10))
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert image.getpixel((0, 16)) == (255, 255, 255)
    assert any(
        image.getpixel((x, y)) == (255, 255, 255) for x in range(4, 85) for y in range(2, 14)
    )


def test_page_cycle_includes_home_and_archive_only_when_available(view):
    for screen in ("weather", "details", "diagnostics", "home"):
        view.next_screen()
        assert view.screen == screen
        assert view.page_badge.text.endswith("/4")
    for screen in ("weather", "details", "diagnostics", "archive", "home"):
        view.next_screen(has_archive=True)
        assert view.screen == screen
        assert view.page_badge.text.endswith("/5")


@pytest.mark.parametrize("screen", ["home", "weather", "details", "diagnostics", "archive"])
def test_only_one_page_is_visible_and_footer_consistent(view, screen):
    context(view)
    show(view, screen)
    groups = (view.home_group, view.weather_group, view.details_group, view.diagnostics_group)
    assert sum(not group.hidden for group in groups) == 1
    assert view.home_group.hidden == (screen != "home")
    assert view.weather_group.hidden == (screen not in ("weather", "archive"))
    assert view.footer.text == "A Metric B Range C LEDs D Page"
    assert_layout(view)


def test_home_shortcut_preserves_selected_graph_and_range(view):
    show(view, "weather")
    view.graph_index = 3
    view.resolution_index = 4
    view.home()
    assert view.screen == "home"
    assert view.graph_index == 3 and view.resolution_index == 4
    view.next_graph_type()
    assert view.screen == "weather" and view.graph_index == 3
    view.home()
    view.cycle_resolution()
    assert view.screen == "weather" and view.resolution_index == 4


def test_graph_controls_cycle_after_open_and_archive_keeps_selected_page(view):
    view.next_graph_type()
    assert view.screen == "weather" and view.graph_index == 0
    view.next_graph_type()
    view.cycle_resolution()
    assert view.graph_index == 1 and view.resolution_index == 3
    show(view, "archive")
    view.next_graph_type()
    view.cycle_resolution()
    assert view.screen == "archive" and view.graph_index == 2 and view.resolution_index == 4


@pytest.mark.parametrize("screen", ["home", "details", "diagnostics"])
def test_hidden_graph_never_scans_history(view, screen):
    show(view, screen)
    history = DataHistory()
    history.summarize = lambda *_: pytest.fail("Hidden graph must not be recomputed")
    view.update_graph(history)


def test_large_graph_has_relative_time_markers_and_missing_current_value(view):
    show(view, "weather")
    history = DataHistory()
    view.update_graph(history)
    assert view.value.text == "Now --"
    assert view.graph_height >= 50
    assert (view.graph_start.text, view.graph_middle.text, view.graph_end.text) == (
        "-12h",
        "-6h",
        "now",
    )
    assert "No usable readings" in view.graph_message.text
    history.add_reading(5, 1013, 70, 40, 0)
    view.graph_dirty = True
    view.update_graph(history)
    assert any(view.bitmap.pixels)
    assert view.value.text == "Now 5.0"
    assert view.graph_message.text == ""
    assert_layout(view)


def test_graph_preserves_short_spikes_and_does_not_join_missing_columns(view):
    view.graph_index = 1  # No PM threshold reference lines in this graph.
    columns = [None] * view.graph_width
    columns[0], columns[-1] = (1000, 1010), (1002, 1002)
    view.draw_graph(columns, 1000, 1010)
    assert all(view.bitmap.pixels[y * view.graph_width] == 1 for y in range(view.graph_height))
    assert all(
        view.bitmap.pixels[y * view.graph_width + 100] == 0 for y in range(view.graph_height)
    )


def test_celsius_conversion_matches_dashboard_graph_and_dew_point(view):
    view.toggle_temperature_unit()
    view.update_sensors(sensors(), "steady", "Fine")
    details(view)
    assert view.temperature.text == "20.0" and view.temperature_units.text == "C"
    show(view, "weather")
    view.graph_index = 2
    history = DataHistory()
    history.add_reading(12, 1013, 68, 40, 0)
    view.update_graph(history)
    assert view.value.text == "Now 20.0" and view.unit.text == "C"
    assert view.stats.text == "Avg 20.0"
    assert_layout(view)
    show(view, "details")
    assert "Dew point: 10.0 C" in visible_text(view)
    view.toggle_temperature_unit()
    assert "Dew point: 50.0 F" in visible_text(view)
    assert_layout(view)


def test_archive_is_explicit_and_never_presents_saved_values_as_current(view):
    context(view)
    show(view, "archive")
    history = DataHistory()
    history.add_reading(77, 1002, 68, 50, 0)
    view.update_graph(history)
    assert view.head.text == "PREVIOUS PM2.5 12h"
    assert view.value.text == "End 77.0"
    assert view.graph_end.text == "end"
    assert view.outlook.text == "Saved session; gap duration unknown"
    assert_layout(view)


def test_archive_identity_survives_simultaneous_alert_and_acknowledgement(view):
    context(view)
    details(view, alerts="Alert: PM high, Pressure change")
    show(view, "archive")
    view.acknowledge("LED brightness: 100%", 160)
    assert view.head.text.startswith("ARCHIVE | ALERT:")
    assert view.outlook.text == "Saved session | LED brightness: 100%"
    view.acknowledge("Very long acknowledgement " * 10, 161)
    assert view.outlook.text.startswith("Saved session | ")
    assert_layout(view)


def test_two_failed_sensors_show_both_ages_without_truncating_pm_caption(view):
    view.set_context(
        3600,
        (
            {"state": "read failed", "errors": 12, "last_good": 3300},
            {"state": "read failed", "errors": 5, "last_good": 2100},
        ),
        3600,
    )
    assert view.home_age.text == "BME retry last 25m; PM retry last 5m"
    assert_layout(view)


def test_sensor_caption_distinguishes_initializing_from_never_successful_offline(view):
    view.set_context(
        10,
        (
            {"state": "unavailable", "errors": 1, "last_good": None},
            {"state": "initializing", "errors": 0, "last_good": None},
        ),
        10,
    )
    assert view.home_age.text == "BME starting; PM offline; never"
    assert_layout(view)


def test_diagnostics_distinguish_failed_attempt_from_last_good_sensor_reading(view):
    show(view, "diagnostics")
    view.update_diagnostics(
        {
            "health": (
                {"state": "retrying", "errors": 12, "last_good": 100},
                {"state": "ready", "errors": 1, "last_good": 159},
            ),
            "memory": None,
            "uptime": 86400,
            "samples": 1440,
            "reset": "POWER_ON",
            "storage": "saved; device logging",
            "clock": "session (RTC unset)",
            "watchdog": "enabled",
            "sample_age": "0s",
        },
        160,
    )
    text = visible_text(view)
    assert "PM: retrying; good 1m; errors 12" in text
    assert "BME: ready; good 1s; errors 1" in text
    assert "WDT: enabled; sample attempt 0s" in text
    assert "Storage: saved; device logging" in text
    assert_layout(view)


def test_long_runtime_indicators_are_clipped_without_overlapping_footer(view):
    show(view, "details")
    view.update_status(
        {
            key: "Sensor unavailable " * 8
            for key in ("aqi_reason", "pressure_reason", "pm_mean_reason", "dew_reason")
        }
    )
    details(view, dew_c=None, pm_mean=None, pressure_delta=None, estimate={"aqi": None})
    assert_layout(view)
    view.acknowledge("LED brightness changed " * 8, 0)
    assert_layout(view)


def test_hidden_nested_groups_are_skipped_and_group_transforms_accumulate():
    outer = Group(x=2, y=3, scale=2)
    inner = Group(x=4, y=5, scale=3)
    label = Label(None, text="visible", x=1, y=2, scale=1)
    hidden = Group()
    hidden.hidden = True
    hidden.append(Label(None, text="hidden", x=0, y=0, scale=1))
    inner.extend((label, hidden))
    outer.append(inner)
    assert list(visible_items(outer)) == [(label, 10, 13, 6)]
    inner.hidden = True
    assert list(visible_items(outer)) == []
