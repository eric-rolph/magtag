from types import SimpleNamespace

import weather_config as config
from weather_core import DataHistory
from weather_ui import UIController, status_messages


def press(controller, button, now, duration=0.2):
    state = [False] * 4
    state[button] = True
    actions = controller.update(state, now)
    actions += controller.update(state, now + 0.06)
    state[button] = False
    actions += controller.update(state, now + duration)
    actions += controller.update(state, now + duration + 0.06)
    return actions


def test_rapid_navigation_has_one_inflight_and_only_one_queued_intent():
    controller = UIController()
    assert press(controller, 0, 0) == ["metric"]
    for index in range(20):
        assert press(controller, 0, 1 + index) == []
    assert controller.pending_navigation == "metric"
    assert press(controller, 1, 30) == []
    assert controller.pending_navigation == "range"
    controller.rendered()
    assert controller.update([False] * 4, 31) == ["range"]
    assert controller.pending_navigation is None
    controller.rendered()
    assert controller.update([False] * 4, 32) == []


def test_short_d_pages_on_release_long_d_returns_home_and_cancels_queue():
    controller = UIController()
    assert controller.update([False, False, False, True], 0) == []
    assert controller.update([False, False, False, True], 0.06) == []
    assert controller.update([False] * 4, 0.2) == []
    assert controller.update([False] * 4, 0.26) == ["page"]
    assert press(controller, 0, 1) == []
    assert controller.pending_navigation == "metric"
    assert controller.update([False, False, False, True], 2) == []
    assert controller.update([False, False, False, True], 2.06) == []
    assert controller.update([False, False, False, True], 3.3) == ["home"]
    assert controller.pending_navigation is None
    assert controller.update([False, False, False, True], 6) == []
    assert controller.update([False] * 4, 6.1) == []
    assert controller.update([False] * 4, 6.2) == []


def test_short_c_brightness_long_c_units_without_short_action_or_repeat():
    controller = UIController()
    assert press(controller, 2, 0) == ["brightness"]
    state = [False, False, True, False]
    controller.update(state, 1)
    controller.update(state, 1.06)
    assert controller.update(state, 2.3) == ["units"]
    assert controller.update(state, 9) == []
    assert controller.update([False] * 4, 9.1) == []
    assert controller.update([False] * 4, 9.2) == []


def test_button_bounce_and_release_before_hold_deadline_do_not_trigger_long_action():
    controller = UIController()
    state = [False, False, True, False]
    assert controller.update(state, 0) == []
    assert controller.update([False] * 4, 0.03) == []
    assert controller.update([False] * 4, 0.1) == []
    controller.update(state, 1)
    controller.update(state, 1.06)
    assert controller.update([False] * 4, 2.2) == []
    assert controller.update([False] * 4, 2.3) == ["brightness"]


def test_status_distinguishes_warmup_missing_sensor_and_recent_coverage():
    history = DataHistory(181)
    sensors = SimpleNamespace(pm=5, pressure=1000, temperature_c=20, humidity=40)
    history.add_reading(5, 1000, 68, 40, 0)
    estimate = {"aqi": None, "reason": "need 2 of last 3 hours", "valid_hours": 11}
    messages = status_messages(history, sensors, estimate, config, None, None, 9)
    assert messages["aqi_reason"] == "Est AQI: need 2 usable recent hours"
    assert messages["pm_mean_reason"] == "PM mean: collecting 1 / 4 readings"
    assert messages["pressure_reason"] == "Pressure: collecting 0 / 180 min"
    sensors.pm = sensors.pressure = sensors.temperature_c = None
    messages = status_messages(history, sensors, estimate, config, None, None, None)
    assert messages["pm_mean_reason"] == "PM sensor unavailable"
    assert messages["pressure_reason"] == "Pressure sensor unavailable"
    assert messages["aqi_reason"] == "Est AQI: PM sensor unavailable"
    assert messages["dew_reason"] == "Dew: temperature unavailable"
    sensors.pressure = 1000
    for slot in range(1, 181):
        history.add_reading(5, None, 68, 40, slot * 60)
    messages = status_messages(history, sensors, estimate, config, 5, None, 9)
    assert messages["pressure_reason"] == "Pressure: insufficient recent coverage"
    assert messages["pm_mean_reason"] == ""


def test_dew_status_distinguishes_missing_humidity_zero_and_estimate_range():
    history = DataHistory(10)
    sensors = SimpleNamespace(pm=5, pressure=1000, temperature_c=20, humidity=None)
    estimate = {"aqi": None, "reason": "need 2 of last 3 hours"}
    messages = status_messages(history, sensors, estimate, config, 5, 0, None)
    assert messages["dew_reason"] == "Dew: humidity unavailable"
    sensors.humidity = 0
    messages = status_messages(history, sensors, estimate, config, 5, 0, None)
    assert messages["dew_reason"] == "Dew: humidity must exceed zero"
    sensors.humidity = 40
    messages = status_messages(history, sensors, estimate, config, 5, 0, None)
    assert messages["dew_reason"] == "Dew: outside estimate range"
