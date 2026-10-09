# SPDX-License-Identifier: MIT
"""Derived sensor estimates; bounded-memory calculations without hardware imports.

PM NowCast follows EPA's May 2026 AQI Technical Assistance Document, pp. 14-17:
https://document.airnow.gov/technical-assistance-document-for-the-reporting-of-daily-air-quailty.pdf
The default uses its 12 latest COMPLETED UTC hours, not the partial current
hour. Explicit session mode instead uses completed elapsed hours since boot;
negative timestamps from a previous boot are excluded. Each hourly mean needs
75% of the scheduled samples (a station policy).
The most recent completed hour has weight exponent zero. Missing hours keep
their real ages and are omitted from both weighted sums. An uncalibrated local
sensor produces an estimated NowCast AQI, not an official AirNow observation.
"""

import math
import sys

from weather_core import valid_number


def dew_point_c(temp_c, humidity):
    """Estimate dew point with the August-Roche-Magnus approximation.

    Constants 17.625 and 243.04 match EPA's HMS humidity algorithm:
    https://qed.epa.gov/hms/meteorology/humidity/algorithms/
    Temperature must be in the BME680 sensor range and RH in (0, 100].
    This is a weather approximation, including at subfreezing temperatures.
    """
    if not (
        valid_number(temp_c, -40, 85)
        and valid_number(humidity, 0, 100)
        and humidity > 0
    ):
        return None
    gamma = math.log(humidity / 100.0) + 17.625 * temp_c / (243.04 + temp_c)
    return min(temp_c, 243.04 * gamma / (17.625 - gamma))


def temperature_from_f(value, unit="F"):
    """Convert a stored Fahrenheit value for display, preserving missing data."""
    if unit not in ("F", "C"):
        raise ValueError("Temperature unit must be F or C")
    if not valid_number(value, -459.67, 1e12):
        return None
    return value if unit == "F" else (value - 32.0) / 1.8


def pm_average(history, seconds=300, minimum_coverage=0.8):
    """Mean PM2.5 over the newest time slots, requiring usable window coverage.

    Five minutes at a one-minute cadence means the five newest slots. Missing
    startup slots count against coverage; unavailable values are never zero.
    """
    if not valid_number(seconds, 1, 1e9):
        raise ValueError("Average window must be positive")
    if not valid_number(minimum_coverage, 0, 1) or minimum_coverage == 0:
        raise ValueError("Average coverage must be greater than zero and at most 1")
    samples = int(math.ceil(seconds / history.interval_seconds))
    total = 0
    valid = 0
    for ago in range(min(samples, history.data_count)):
        value = history.value_ago(0, ago)
        if valid_number(value, 0, 3276.7):
            total += int(value * 10 + 0.5)
            valid += 1
    return total / (valid * 10) if valid and valid / samples >= minimum_coverage else None


def pressure_change(history, seconds=10800, max_gap_seconds=300):
    """Actual endpoint pressure change, using the same eligibility as trend.

    Require both endpoints, 80% usable slots, and no missing run longer than
    max_gap_seconds. At non-dividing cadences the span rounds up to a slot.
    """
    if not valid_number(seconds, 1, 1e9) or not valid_number(max_gap_seconds, 0, 1e9):
        raise ValueError("Pressure window and gap must be valid seconds")
    intervals = int(math.ceil(seconds / history.interval_seconds))
    if history.data_count <= intervals:
        return None
    newest = history.value_ago(1)
    oldest = history.value_ago(1, intervals)
    if newest is None or oldest is None:
        return None
    valid = 0
    gap = 0
    for ago in range(intervals + 1):
        if history.value_ago(1, ago) is None:
            gap += 1
            if gap * history.interval_seconds > max_gap_seconds:
                return None
        else:
            valid += 1
            gap = 0
    if valid / (intervals + 1) < 0.8:
        return None
    series = history._series[1]
    newest_encoded = series[(history.write_index - 1) % history.max_samples]
    oldest_encoded = series[(history.write_index - 1 - intervals) % history.max_samples]
    return (newest_encoded - oldest_encoded) / 10.0


# Concentrations are stored as integer tenths to avoid boundary drift.
_PM_BREAKPOINTS = (
    (0, 90, 0, 50),
    (91, 354, 51, 100),
    (355, 554, 101, 150),
    (555, 1254, 151, 200),
    (1255, 2254, 201, 300),
    (2255, 3254, 301, 500),
)

# CircuitPython 7 uses 30-bit float objects: even a literal such as 9.1 can be
# represented just below 9.1. Correct only that platform's representational
# error at a tenth boundary; desktop calculations retain their finer precision.
_FLOAT_EPSILON = 1e-6 if sys.implementation.name == "circuitpython" else 1e-12


def _concentration_tenths(value):
    return int(value * 10 + max(1e-7, value * 10 * _FLOAT_EPSILON))


def _aqi_from_tenths(tenth):
    """Interpolate using integer arithmetic, with exact round-half-up behavior."""
    for lower, upper, index_lower, index_upper in _PM_BREAKPOINTS:
        if tenth <= upper:
            width = upper - lower
            numerator = (index_upper - index_lower) * (tenth - lower)
            return index_lower + (2 * numerator + width) // (2 * width)
    lower, upper, index_lower, index_upper = _PM_BREAKPOINTS[-1]
    width = upper - lower
    numerator = (index_upper - index_lower) * (tenth - lower)
    return max(501, index_lower + (2 * numerator + width) // (2 * width))


def _category_from_aqi(aqi):
    # Match the existing station's compact descriptors and LED colors.
    for upper, name, color in (
        (50, "Good", (0, 128, 0)),
        (100, "Moderate", (225, 225, 0)),
        (150, "Sensitive", (255, 126, 0)),
        (200, "Unhealthy", (200, 0, 0)),
        (300, "V.Unhealthy", (128, 0, 128)),
    ):
        if aqi <= upper:
            return name, color
    return "Hazardous", (100, 0, 0)


def concentration_to_aqi(concentration):
    """PM2.5 AQI conversion: truncate 0.1 ug/m3, then round half up.

    Uses EPA's 2024 breakpoints retained in the May 2026 guide. Above 325.4,
    extrapolate the highest segment and return at least 501; callers must label
    these above-range sensor estimates. Invalid sensor inputs return None.
    """
    if not valid_number(concentration, 0, 3276.7):
        return None
    return _aqi_from_tenths(_concentration_tenths(concentration))


def _pending_nowcast(reason, valid_hours=0, time_basis="utc"):
    return {
        "concentration": None,
        "aqi": None,
        "category": "Unknown",
        "color": (100, 100, 100),
        "valid_hours": valid_hours,
        "reason": reason,
        "time_basis": time_basis,
    }


def nowcast(history, epoch_of_latest_slot=None, hourly_coverage=0.75, time_basis="utc"):
    """Estimated PM2.5 NowCast from completed hourly averages.

    epoch_of_latest_slot is UTC epoch seconds for the latest HISTORY SLOT,
    not the time at which this function is called or a delayed sensor read.
    In default UTC mode any consistent UTC epoch origin on an hour boundary
    works. Without a trustworthy clock UTC mode reports no AQI. Explicit
    time_basis="session" treats this argument as the latest slot's elapsed
    seconds since boot instead, bins complete [0, 3600), [3600, 7200), etc.,
    and excludes prior-session readings at negative times. This is labelled
    a session estimate, since its hourly boundaries can differ from AirNow.
    Storage allocation stays at 12 hourly means, independent of retention.
    """
    if time_basis not in ("utc", "session"):
        raise ValueError("NowCast time basis must be utc or session")
    if not valid_number(hourly_coverage, 0, 1) or hourly_coverage == 0:
        raise ValueError("Hourly coverage must be greater than zero and at most 1")
    if not valid_number(epoch_of_latest_slot, 0, 1e12):
        return _pending_nowcast("pending clock", time_basis=time_basis)
    if not history.data_count:
        return _pending_nowcast("need 2 of last 3 hours", time_basis=time_basis)
    interval = history.interval_seconds
    latest_hour = int(epoch_of_latest_slot // 3600) * 3600
    hourly = []
    valid_hours = 0
    minimum = maximum = None
    for hour_ago in range(12):
        end = latest_hour - hour_ago * 3600
        start = end - 3600
        if time_basis == "session" and start < 0:
            hourly.append(None)
            continue
        # Include [start, end), counting the actual scheduled cadence even if
        # its interval does not divide an hour or starts off the minute.
        first_ago = int(math.floor((epoch_of_latest_slot - end) / interval)) + 1
        last_ago = int(math.floor((epoch_of_latest_slot - start) / interval))
        expected = last_ago - first_ago + 1
        valid = 0
        total = 0
        for ago in range(first_ago, min(last_ago + 1, history.data_count)):
            value = history.value_ago(0, ago)
            if valid_number(value, 0, 3276.7):
                # History is encoded to tenths. Recover that integer once so
                # summing 60 readings cannot accumulate float rounding bias.
                total += int(value * 10 + 0.5)
                valid += 1
        value = total / valid if valid and valid / expected >= hourly_coverage else None
        hourly.append(value)
        if value is not None:
            valid_hours += 1
            minimum = value if minimum is None else min(minimum, value)
            maximum = value if maximum is None else max(maximum, value)
    if sum(value is not None for value in hourly[:3]) < 2:
        return _pending_nowcast("need 2 of last 3 hours", valid_hours, time_basis)
    weight = max(0.5, minimum / maximum) if maximum else 1.0
    numerator = denominator = 0.0
    factor = 1.0
    for value in hourly:
        if value is not None:
            numerator += factor * value
            denominator += factor
        factor *= weight
    weighted_tenths = numerator / denominator
    tenth = int(weighted_tenths + max(1e-7, weighted_tenths * _FLOAT_EPSILON))
    concentration = tenth / 10.0
    aqi = _aqi_from_tenths(tenth)
    category, color = _category_from_aqi(aqi)
    reason = "session estimate" if time_basis == "session" else "sensor estimate"
    if tenth > 3254:
        reason = (
            "above-range session estimate" if time_basis == "session" else "above-range estimate"
        )
    return {
        "concentration": concentration,
        "aqi": aqi,
        "category": category,
        "color": color,
        "valid_hours": valid_hours,
        "reason": reason,
        "time_basis": time_basis,
    }
