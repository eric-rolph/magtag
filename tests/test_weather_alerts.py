import math
from types import SimpleNamespace

import pytest

import weather_config
from weather_alerts import AlertManager, SustainedAlert


def test_activation_requires_continuous_threshold_duration():
    alert = SustainedAlert(35.5, 30, 300)
    assert alert.state == "unknown"
    assert not alert.update(35.5, 0)
    assert alert.state == "pending"
    for now in (60, 120, 180, 240, 299):
        assert not alert.update(35.5, now)
    assert alert.update(35.5, 300)
    assert alert.state == "active"
    assert not alert.unknown


def test_brief_crossing_cancels_pending_activation():
    alert = SustainedAlert(35.5, 30, 120)
    alert.update(40, 0)
    alert.update(35.4, 60)
    assert alert.state == "clear"
    alert.update(40, 120)
    assert not alert.update(40, 180)
    assert alert.update(40, 240)


def test_hysteresis_and_sustained_clearing_at_exact_boundary():
    alert = SustainedAlert(35.5, 30, 60, clear_duration_seconds=120)
    alert.update(40, 0)
    assert alert.update(40, 60)
    assert alert.update(32, 120)
    assert alert.state == "active"
    assert alert.update(30, 180)
    assert alert.state == "clearing"
    assert alert.update(30.1, 240)
    assert alert.state == "active"
    assert alert.update(30, 300)
    assert alert.update(30, 360)
    assert not alert.update(30, 420)
    assert alert.state == "clear"


@pytest.mark.parametrize("missing", [None, math.nan, math.inf, -1, "40", True])
def test_unknown_samples_cancel_pending_and_unknown_time_never_counts(missing):
    alert = SustainedAlert(35.5, 30, 120)
    alert.update(40, 0)
    assert not alert.update(missing, 60)
    assert alert.unknown and alert.state == "unknown"
    assert not alert.update(40, 120)
    assert not alert.update(40, 180)
    assert alert.update(40, 240)


def test_unknown_sample_preserves_condition_but_cancels_clearing_timer():
    alert = SustainedAlert(35.5, 30, 60)
    alert.update(40, 0)
    alert.update(40, 60)
    alert.update(20, 120)
    assert alert.update(None, 180)
    assert alert.unknown
    assert alert.update(20, 240)
    assert alert.state == "clearing"
    assert alert.update(20, 300)
    assert not alert.update(20, 360)


def test_prolonged_gaps_reset_activation_and_clearing_timers():
    alert = SustainedAlert(35.5, 30, 120, maximum_gap_seconds=90)
    alert.update(40, 0)
    assert not alert.update(40, 600)
    assert not alert.update(40, 660)
    assert alert.update(40, 720)
    alert.update(20, 780)
    assert alert.update(20, 1380)
    assert alert.update(20, 1440)
    assert not alert.update(20, 1500)


def test_backward_clock_resets_pending_and_invalid_clock_marks_unknown():
    alert = SustainedAlert(35.5, 30, 120)
    alert.update(40, 100)
    alert.update(40, 160)
    assert not alert.update(40, 50)
    assert not alert.update(40, 110)
    assert alert.update(40, 170)
    assert alert.update(20, math.nan)
    assert alert.unknown


def test_repeated_identical_timestamps_do_not_accumulate_duration():
    alert = SustainedAlert(35.5, 30, 60)
    for _ in range(100):
        assert not alert.update(40, 0)
    assert alert.update(40, 60)


@pytest.mark.parametrize("arguments", [
    (0, 0, 60), (35, 35, 60), (35, -1, 60), (35, 30, 0), (math.inf, 30, 60),
    (35, 30, 60, 0), (35, 30, 60, 120, 0),
])
def test_invalid_alert_configuration_is_rejected(arguments):
    with pytest.raises(ValueError):
        SustainedAlert(*arguments)


def config(**overrides):
    values = {name: getattr(weather_config, name) for name in dir(weather_config) if name.isupper()}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_manager_uses_absolute_pressure_change_and_known_active_names():
    alerts = AlertManager(config(PM_ALERT_DURATION_SECONDS=60, PRESSURE_ALERT_DURATION_SECONDS=60))
    assert alerts.text == "Alerts: data pending"
    assert alerts.update(40, -3, 0) == ()
    assert alerts.update(40, -3, 60) == ("PM high", "Pressure change")
    assert alerts.text == "Alert: PM high, Pressure change"
    assert alerts.update(None, None, 120) == ()
    assert alerts.text == "Alerts: data pending"
    assert alerts.pm.active and alerts.pm.unknown
    assert alerts.update(40, 0, 180) == ("PM high", "Pressure change")
    assert alerts.update(40, 0, 240) == ("PM high", "Pressure change")
    assert alerts.update(40, 0, 300) == ("PM high",)


def test_manager_clear_pending_and_disabled_messages():
    alerts = AlertManager(config())
    assert alerts.update(5, 1, 0) == ()
    assert alerts.text == "Alerts: clear"
    alerts.update(5, None, 60)
    assert alerts.text == "Alerts: data pending"
    disabled = AlertManager(config(ALERT_ENABLED=False))
    assert disabled.update(500, 20, 0) == ()
    assert disabled.update(500, 20, 600) == ()
    assert disabled.text == "Alerts: disabled"
