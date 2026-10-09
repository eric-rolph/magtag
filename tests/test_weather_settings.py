import math
from types import SimpleNamespace

import pytest

import weather_config
from weather_settings import validate_config


def settings(**overrides):
    values = {
        name: getattr(weather_config, name)
        for name in dir(weather_config) if name.isupper()
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_default_settings_are_valid_and_validation_does_not_mutate():
    config = settings()
    original = vars(config).copy()
    assert validate_config(config) == []
    assert vars(config) == original
    assert validate_config(weather_config) == []


def test_validation_reports_independent_settings_together():
    errors = validate_config(settings(
        TEMPERATURE_UNIT="K", ELEVATION_M=math.nan, TEMPERATURE_OFFSET_C=math.inf,
        PRESSURE_OFFSET_HPA="0", SENSOR_RESET_AFTER_ERRORS=0, BUTTON_DEBOUNCE_SECONDS=0,
        SENSOR_RETRY_SECONDS=-1, POLL_INTERVAL_SECONDS=False,
        LED_BRIGHTNESS_LEVELS=(), BME280_ADDRESSES=[0x77],
    ))
    for name in (
        "TEMPERATURE_UNIT", "ELEVATION_M", "TEMPERATURE_OFFSET_C", "PRESSURE_OFFSET_HPA",
        "SENSOR_RESET_AFTER_ERRORS", "BUTTON_DEBOUNCE_SECONDS", "SENSOR_RETRY_SECONDS",
        "POLL_INTERVAL_SECONDS", "LED_BRIGHTNESS_LEVELS", "BME280_ADDRESSES",
    ):
        assert any(name in error for error in errors)


@pytest.mark.parametrize("value", [0, -1, 20161, 181.0, True, math.inf])
def test_history_capacity_is_an_integer_with_memory_and_duration_bounds(value):
    errors = validate_config(settings(HISTORY_SAMPLES=value))
    assert any("HISTORY_SAMPLES" in error for error in errors)


def test_history_must_cover_three_hour_endpoints_and_configured_trend():
    small_presets = (1, 2, 3)
    assert any("HISTORY_SAMPLES" in error for error in validate_config(settings(
        HISTORY_SAMPLES=180, RESOLUTION_HOURS=small_presets, DEFAULT_GRAPH_HOURS=3,
    )))
    assert validate_config(settings(
        HISTORY_SAMPLES=181, RESOLUTION_HOURS=small_presets, DEFAULT_GRAPH_HOURS=3,
    )) == []
    assert any("HISTORY_SAMPLES" in error for error in validate_config(settings(
        HISTORY_SAMPLES=181, TREND_WINDOW_SECONDS=14400,
        RESOLUTION_HOURS=small_presets, DEFAULT_GRAPH_HOURS=3,
    )))


@pytest.mark.parametrize("presets", [(), (1, 0), (1, math.nan), (6, 1), (1, 1), (1, 169)])
def test_graph_presets_are_safe_and_fit_history(presets):
    assert any("RESOLUTION_HOURS" in error for error in validate_config(settings(
        RESOLUTION_HOURS=presets,
    )))


def test_default_graph_brightness_and_retry_ranges():
    errors = validate_config(settings(
        DEFAULT_GRAPH_HOURS=2, LED_BRIGHTNESS_INDEX=8, TREND_MAX_GAP_SECONDS=59,
    ))
    assert any("DEFAULT_GRAPH_HOURS" in error for error in errors)
    assert any("LED_BRIGHTNESS_INDEX" in error for error in errors)
    assert any("TREND_MAX_GAP_SECONDS" in error for error in errors)
    assert validate_config(settings(LED_BRIGHTNESS_INDEX=0, TEMPERATURE_UNIT="C")) == []


@pytest.mark.parametrize("interval", [0.5, 3601, 10 ** 1000])
def test_sampling_interval_matches_history_constructor_limits(interval):
    errors = validate_config(settings(READING_INTERVAL_SECONDS=interval))
    assert any("READING_INTERVAL_SECONDS" in error for error in errors)


@pytest.mark.parametrize("elevation", [-501, 9001])
def test_elevation_matches_pressure_correction_limits(elevation):
    assert any("ELEVATION_M" in error for error in validate_config(settings(ELEVATION_M=elevation)))


@pytest.mark.parametrize("levels", [(0, 1.01), (-0.01, 1), (0, True), (0, math.inf)])
def test_brightness_rejects_outside_range_and_non_numbers(levels):
    assert any("LED_BRIGHTNESS_LEVELS" in error for error in validate_config(settings(
        LED_BRIGHTNESS_LEVELS=levels,
    )))


@pytest.mark.parametrize("addresses", [(), (0x77, 0x77), (0x00,), ("0x77",), (True,)])
def test_addresses_are_valid_unique_bme280_addresses(addresses):
    assert any("BME280_ADDRESSES" in error for error in validate_config(settings(
        BME280_ADDRESSES=addresses,
    )))


@pytest.mark.parametrize("directory", ["", "/", "weather-state", "/../state", "/state\x00", None])
def test_checkpoint_directory_rejects_unsafe_paths(directory):
    assert any("CHECKPOINT_DIRECTORY" in error for error in validate_config(settings(
        CHECKPOINT_DIRECTORY=directory,
    )))


def test_new_settings_are_validated_together():
    errors = validate_config(settings(
        PERSISTENCE_ENABLED=1, WATCHDOG_ENABLED="yes", ALERT_ENABLED=None,
        CHECKPOINT_INTERVAL_SECONDS=0, WATCHDOG_TIMEOUT_SECONDS=math.inf,
        DIAGNOSTIC_REFRESH_SECONDS=-1, PM_AVERAGE_SECONDS=0,
        HOURLY_MINIMUM_COVERAGE=1.1,
        PM_ALERT_THRESHOLD=30, PM_ALERT_CLEAR_THRESHOLD=30,
        PRESSURE_ALERT_THRESHOLD_HPA=2, PRESSURE_ALERT_CLEAR_THRESHOLD_HPA=3,
        PM_ALERT_DURATION_SECONDS=0, PRESSURE_ALERT_DURATION_SECONDS=0,
        ALERT_CLEAR_DURATION_SECONDS=-1,
    ))
    for name in (
        "PERSISTENCE_ENABLED", "WATCHDOG_ENABLED", "ALERT_ENABLED",
        "CHECKPOINT_INTERVAL_SECONDS", "WATCHDOG_TIMEOUT_SECONDS", "DIAGNOSTIC_REFRESH_SECONDS",
        "PM_AVERAGE_SECONDS", "HOURLY_MINIMUM_COVERAGE", "PM_ALERT_CLEAR_THRESHOLD",
        "PRESSURE_ALERT_CLEAR_THRESHOLD_HPA", "PM_ALERT_DURATION_SECONDS",
        "PRESSURE_ALERT_DURATION_SECONDS", "ALERT_CLEAR_DURATION_SECONDS",
    ):
        assert any(name in error for error in errors)


def test_missing_settings_produce_errors_instead_of_crashing():
    errors = validate_config(SimpleNamespace())
    assert len(errors) >= 30
    assert all(isinstance(error, str) for error in errors)
