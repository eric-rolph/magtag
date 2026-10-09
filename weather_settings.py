# SPDX-License-Identifier: MIT
"""Validate user settings before allocating buffers or starting device hardware."""

import math


def _finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def validate_config(config):
    """Return every detected configuration problem as a readable string.

    Modules and attribute-based settings objects are both supported. Validation does
    not change settings, allocate history buffers, or access hardware.
    """
    errors = []
    missing = object()

    def number(name, positive=False, nonnegative=False, integer=False):
        value = getattr(config, name, missing)
        if not _finite_number(value) or (integer and not isinstance(value, int)):
            kind = "integer" if integer else "number"
            errors.append("{} must be a finite {}.".format(name, kind))
            return None
        if positive and value <= 0:
            errors.append("{} must be greater than zero.".format(name))
            return None
        if nonnegative and value < 0:
            errors.append("{} must be zero or greater.".format(name))
            return None
        return value

    for name in ("ELEVATION_M", "TEMPERATURE_OFFSET_C", "PRESSURE_OFFSET_HPA"):
        number(name)
    elevation = getattr(config, "ELEVATION_M", missing)
    if _finite_number(elevation) and not -500 <= elevation <= 9000:
        errors.append("ELEVATION_M must be between -500 and 9000 for pressure correction.")
    if getattr(config, "TEMPERATURE_UNIT", missing) not in ("C", "F"):
        errors.append('TEMPERATURE_UNIT must be "C" or "F".')

    interval = number("READING_INTERVAL_SECONDS", positive=True)
    if interval is not None and not 1 <= interval <= 3600:
        errors.append("READING_INTERVAL_SECONDS must be between 1 and 3600.")
    capacity = number("HISTORY_SAMPLES", positive=True, integer=True)
    trend_window = number("TREND_WINDOW_SECONDS", positive=True)
    number("TREND_THRESHOLD_HPA", positive=True)
    trend_gap = number("TREND_MAX_GAP_SECONDS", positive=True)
    if capacity is not None and capacity > 20160:
        errors.append("HISTORY_SAMPLES must be at most 20160 to bound device memory use.")
    if interval is not None and capacity is not None:
        required_span = max(10800, trend_window or 10800)
        if (capacity - 1) * interval < required_span:
            errors.append("HISTORY_SAMPLES must retain at least three hours and the trend window.")
    if trend_window is not None and trend_window < 10800:
        errors.append("TREND_WINDOW_SECONDS must be at least 10800 for the three-hour forecast.")
    if interval is not None and trend_gap is not None and trend_gap < interval:
        errors.append("TREND_MAX_GAP_SECONDS must be at least READING_INTERVAL_SECONDS.")

    for name in (
        "SENSOR_RETRY_SECONDS", "BUTTON_DEBOUNCE_SECONDS", "POLL_INTERVAL_SECONDS",
        "CHECKPOINT_INTERVAL_SECONDS", "WATCHDOG_TIMEOUT_SECONDS", "DIAGNOSTIC_REFRESH_SECONDS",
        "PM_AVERAGE_SECONDS", "PM_ALERT_DURATION_SECONDS", "PRESSURE_ALERT_DURATION_SECONDS",
        "ALERT_CLEAR_DURATION_SECONDS",
    ):
        number(name, positive=True)
    number("SENSOR_RESET_AFTER_ERRORS", positive=True, integer=True)

    presets = getattr(config, "RESOLUTION_HOURS", missing)
    valid_presets = isinstance(presets, (tuple, list)) and bool(presets)
    if valid_presets:
        valid_presets = all(_finite_number(value) and value > 0 for value in presets)
    if not valid_presets:
        errors.append("RESOLUTION_HOURS must contain positive, finite graph durations.")
    else:
        if any(presets[index] >= presets[index + 1] for index in range(len(presets) - 1)):
            errors.append("RESOLUTION_HOURS must be unique and in increasing order.")
        if capacity is not None and interval is not None:
            if max(presets) * 3600 > capacity * interval:
                errors.append("RESOLUTION_HOURS cannot exceed the configured history capacity.")
    default_hours = getattr(config, "DEFAULT_GRAPH_HOURS", missing)
    if not _finite_number(default_hours) or not valid_presets or default_hours not in presets:
        errors.append("DEFAULT_GRAPH_HOURS must match a RESOLUTION_HOURS preset.")

    levels = getattr(config, "LED_BRIGHTNESS_LEVELS", missing)
    valid_levels = isinstance(levels, (tuple, list)) and bool(levels)
    if valid_levels:
        valid_levels = all(_finite_number(value) and 0 <= value <= 1 for value in levels)
    if not valid_levels:
        errors.append("LED_BRIGHTNESS_LEVELS must contain finite brightness values from 0 to 1.")
    brightness = number("LED_BRIGHTNESS_INDEX", nonnegative=True, integer=True)
    if valid_levels and brightness is not None and brightness >= len(levels):
        errors.append("LED_BRIGHTNESS_INDEX must select an entry in LED_BRIGHTNESS_LEVELS.")

    addresses = getattr(config, "BME280_ADDRESSES", missing)
    if not isinstance(addresses, tuple) or not addresses or any(
        not isinstance(address, int) or isinstance(address, bool) or address not in (0x76, 0x77)
        for address in addresses
    ):
        errors.append("BME280_ADDRESSES must be a nonempty tuple containing 0x76 and/or 0x77.")
    elif len(set(addresses)) != len(addresses):
        errors.append("BME280_ADDRESSES must not contain duplicate addresses.")

    for name in ("PERSISTENCE_ENABLED", "WATCHDOG_ENABLED", "ALERT_ENABLED"):
        if not isinstance(getattr(config, name, missing), bool):
            errors.append("{} must be True or False.".format(name))
    directory = getattr(config, "CHECKPOINT_DIRECTORY", missing)
    if (
        not isinstance(directory, str) or not directory.startswith("/") or directory == "/"
        or ".." in directory.split("/") or "\x00" in directory or "\\" in directory
    ):
        errors.append(
            "CHECKPOINT_DIRECTORY must be an absolute device directory, e.g. /weather-state."
        )
    coverage = number("HOURLY_MINIMUM_COVERAGE", positive=True)
    if coverage is not None and coverage > 1:
        errors.append("HOURLY_MINIMUM_COVERAGE must be greater than zero and at most 1.")
    for activate_name, clear_name in (
        ("PM_ALERT_THRESHOLD", "PM_ALERT_CLEAR_THRESHOLD"),
        ("PRESSURE_ALERT_THRESHOLD_HPA", "PRESSURE_ALERT_CLEAR_THRESHOLD_HPA"),
    ):
        activate = number(activate_name, positive=True)
        clear = number(clear_name, nonnegative=True)
        if activate is not None and clear is not None and clear >= activate:
            errors.append(
                "{} must be lower than {} for hysteresis.".format(clear_name, activate_name)
            )
    return errors
