# SPDX-License-Identifier: MIT
"""Run pure production calculations on CircuitPython 7 itself.

No hardware I/O, filesystem access, clock access, or desktop imports. Execute
this source with production modules importable, then call run(). A test history
uses at most 721 slots (5,768 payload bytes), rather than a full week buffer.
"""

import gc

import weather_config as config
from weather_core import DataHistory, pm_category
from weather_metrics import (
    concentration_to_aqi, dew_point_c, nowcast, pm_average, pressure_change, temperature_from_f,
)
from weather_settings import validate_config


def _hourly_history(newest_first):
    history = DataHistory(721, start_time=0)
    for slot in range(721):
        age = (43199 - slot * 60) // 3600
        pm = newest_first[age] if age >= 0 else 1000
        history.add_reading(pm, 1000, 68, 50, slot * 60)
    return history


class _InvalidConfig:
    ELEVATION_M = -501
    TEMPERATURE_UNIT = "K"
    LED_BRIGHTNESS_INDEX = 999

    def __getattr__(self, name):
        return getattr(config, name)


def run():
    """Return passed/failed counts and named failures; never touches device I/O."""
    report = {"passed": 0, "failed": 0, "failures": [], "max_history_payload_bytes": 5768}

    def check(name, actual, expected, tolerance=None):
        correct = actual == expected
        if tolerance is not None:
            correct = actual is not None and abs(actual - expected) <= tolerance
        if correct:
            report["passed"] += 1
        else:
            report["failed"] += 1
            report["failures"].append(
                "{}: expected {}, observed {}".format(name, expected, actual)
            )

    try:
        for value, expected in (
            (0, 0), (9.0, 50), (9.099, 50), (9.1, 51), (35.4, 100),
            (35.499, 100), (35.5, 101), (35.9, 102), (55.4, 150),
            (55.5, 151), (125.4, 200), (125.5, 201), (225.4, 300),
            (225.5, 301), (325.4, 500), (325.5, 501), (500.4, 849),
        ):
            check("AQI {}".format(value), concentration_to_aqi(value), expected)
        for value, expected in ((9.1, "Moderate"), (35.5, "Sensitive"), (55.5, "Unhealthy")):
            check("current PM category {}".format(value), pm_category(value)[0], expected)
        for value, expected in ((0, 0), (9.1, 51), (35.5, 101), (35.9, 102), (55.5, 151)):
            history = _hourly_history([value] * 12)
            result = nowcast(history, 43200)
            check("constant concentration {}".format(value), result["concentration"], value, 0.01)
            check("constant AQI {}".format(value), result["aqi"], expected)
            check("constant valid hours {}".format(value), result["valid_hours"], 12)
            del history
            gc.collect()
        # Independent published EPA/NWS example: original oldest->newest.
        history = _hourly_history(list(reversed([50, 80, 75, 90, 82, 53, 64, 74, 21, 10, 16, 13])))
        result = nowcast(history, 43200)
        check("published EPA concentration", result["concentration"], 17.4, 0.01)
        check("published EPA AQI", result["aqi"], 66)
        del history
        gc.collect()
        # Published AirNow example retains true ages around a missing hour.
        history = _hourly_history([21, None, 35, 49.2, 48.6, 53.7, 66.2, 69.2, 64.9, 50, 43, 34.9])
        result = nowcast(history, 43200)
        check("published missing-hour concentration", result["concentration"], 28.4, 0.01)
        check("published missing-hour AQI", result["aqi"], 87)
        check("published missing-hour coverage", result["valid_hours"], 11)
        del history
        gc.collect()
        history = _hourly_history([20, 20] + [100] * 10)
        result = nowcast(history, 7200, time_basis="session")
        check("session excludes earlier boot data", result["valid_hours"], 2)
        check("session constant AQI", result["aqi"], 71)
        check("session label", result["reason"], "session estimate")
        del history
        gc.collect()
        check("dew point", dew_point_c(20, 50), 9.2611, 0.01)
        check("zero humidity dew point", dew_point_c(20, 0), None)
        check("Celsius conversion", temperature_from_f(68, "C"), 20, 0.01)
        history = DataHistory(10, start_time=0)
        history.add_reading(1, 1000, 68, 50, 0)
        history.add_reading(3, 1000, 68, 50, 120)
        check("gap slot count", history.data_count, 3)
        check("gap remains missing", history.value_ago(0, 1), None)
        del history
        for delta, expected in ((1.6, "rising"), (-1.6, "falling"), (0, "steady")):
            history = DataHistory(181, start_time=0)
            for slot in range(181):
                pressure = 1000 + delta * slot / 180
                history.add_reading(10, pressure, 68, 50, slot * 60)
            check("pressure trend {}".format(delta), history.pressure_trend(), expected)
            check("pressure delta {}".format(delta), pressure_change(history), delta, 0.00001)
            del history
            gc.collect()
        history = DataHistory(181, start_time=0)
        for slot in range(181):
            pressure = None if 30 <= slot <= 36 else 1000
            history.add_reading(10, pressure, 68, 50, slot * 60)
        check("pressure rejects long gap", history.pressure_trend(), "-")
        del history
        for value in (9.1, 35.5, 35.9, 55.5):
            history = DataHistory(5, start_time=0)
            for slot in range(5):
                history.add_reading(value, 1000, 68, 50, slot * 60)
            check("PM 5m constant {}".format(value), pm_average(history), value, 0.00001)
            del history
        check("default settings valid", len(validate_config(config)), 0)
        errors = " ".join(validate_config(_InvalidConfig()))
        check("invalid elevation caught", "ELEVATION_M" in errors, True)
        check("invalid temperature unit caught", "TEMPERATURE_UNIT" in errors, True)
        check("invalid LED index caught", "LED_BRIGHTNESS_INDEX" in errors, True)
    except Exception as error:
        report["failed"] += 1
        report["failures"].append("self-test raised {}".format(error))
    gc.collect()
    return report
