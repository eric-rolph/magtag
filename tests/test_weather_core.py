import math

import pytest

from weather_core import (
    ButtonTracker,
    DataHistory,
    SamplingSchedule,
    ZambrettiForecaster,
    pm_category,
    sea_level_pressure,
)


@pytest.mark.parametrize(
    "value,category",
    [
        (0, "Good"),
        (9.0, "Good"),
        (9.09, "Good"),
        (9.1, "Moderate"),
        (35.4, "Moderate"),
        (35.5, "Sensitive"),
        (55.4, "Sensitive"),
        (55.5, "Unhealthy"),
        (125.4, "Unhealthy"),
        (125.5, "V.Unhealthy"),
        (225.4, "V.Unhealthy"),
        (225.5, "Hazardous"),
        (None, "Unknown"),
        (-1, "Unknown"),
        (math.nan, "Unknown"),
        (math.inf, "Unknown"),
        ("9", "Unknown"),
    ],
)
def test_pm_boundaries(value, category):
    assert pm_category(value)[0] == category


def test_sea_level_pressure_and_invalid_inputs():
    assert sea_level_pressure(1013.2, 20, 0) == pytest.approx(1013.2)
    assert sea_level_pressure(835, 20, 1609) == pytest.approx(1003.9664, abs=0.01)
    for arguments in (
        (None, 20, 0),
        (835, None, 1609),
        (math.nan, 20, 0),
        (835, -274, 1609),
        (835, 20, math.inf),
    ):
        assert sea_level_pressure(*arguments) is None


def add(history, value, timestamp):
    history.add_reading(value, value, value, value, timestamp)


def test_history_wrap_precision_statistics_and_payload():
    history = DataHistory(3)
    for index, value in enumerate((1.04, 2.06, 3.0, 4.0)):
        add(history, value, index * 60)
    assert [history.value_ago(0, ago) for ago in (2, 1, 0)] == [2.1, 3.0, 4.0]
    low, average, high, valid, count, _ = history.summarize(0, 3, 3)
    assert (low, high, valid, count) == (2.1, 4.0, 3, 3)
    assert average == pytest.approx(9.1 / 3)
    assert history.storage_bytes == 24
    assert DataHistory().storage_bytes == 80640


def test_missing_time_is_not_invented_data_and_same_slot_replaces():
    history = DataHistory(10)
    add(history, 1, 0)
    add(history, 9, 300)
    assert history.data_count == 6
    assert [history.value_ago(0, ago) for ago in range(6)] == [9, None, None, None, None, 1]
    add(history, 10, 310)
    assert history.data_count == 6
    assert history.value_ago(0) == 10
    assert history.summarize(0, 10, 10)[3] == 2
    with pytest.raises(ValueError):
        add(history, 5, 240)


def test_gap_larger_than_capacity_removes_old_data():
    history = DataHistory(3)
    add(history, 1, 0)
    add(history, 4, 600000)
    assert [history.value_ago(0, ago) for ago in range(3)] == [4, None, None]


def test_graph_preserves_dense_spikes_and_places_startup_at_right():
    history = DataHistory(60)
    for index in range(60):
        add(history, 100 if index == 22 else 1, index * 60)
    *_, columns = history.summarize(0, 60, 5)
    assert any(column is not None and column[1] == 100 for column in columns)
    fresh = DataHistory(60)
    add(fresh, 5, 0)
    *_, columns = fresh.summarize(0, 60, 5)
    assert columns == [None, None, None, None, [5, 5]]


def test_invalid_readings_do_not_pollute_statistics():
    history = DataHistory(10)
    for index, value in enumerate((math.nan, math.inf, None, "bad", 1.2, -5.5)):
        add(history, value, index * 60)
    low, average, high, valid, *_ = history.summarize(0, 10, 5)
    assert (low, high, valid) == (-5.5, 1.2, 2)
    assert average == pytest.approx(-2.15)


@pytest.mark.parametrize("delta,trend", [(0, "steady"), (1.6, "rising"), (-1.6, "falling")])
def test_trend_requires_full_three_hour_span(delta, trend):
    history = DataHistory(181)
    for index in range(180):
        add(history, 1000 + delta * index / 180, index * 60)
    assert history.pressure_trend() == "-"
    add(history, 1000 + delta, 10800)
    assert history.pressure_trend() == trend


def test_trend_rejects_gaps_and_recovers_with_fresh_window():
    history = DataHistory(181)
    for index in range(181):
        add(history, None if 30 <= index <= 36 else 1000, index * 60)
    assert history.pressure_trend() == "-"
    for index in range(181, 362):
        add(history, 1000 + (index - 181) / 60, index * 60)
    assert history.pressure_trend() == "rising"
    add(history, None, 362 * 60)
    assert history.pressure_trend() == "-"


@pytest.mark.parametrize(
    "pressure,trend,expected",
    [
        (1050, "falling", "Settled fine"),
        (1030, "falling", "Fine, less settled"),
        (1000, "falling", "Rain, worsening"),
        (995, "falling", "Rain, very unsettled"),
        (985, "falling", "Very unsettled, rain"),
        (1033, "steady", "Settled fine"),
        (1013, "steady", "Fine, possible showers"),
        (960, "steady", "Stormy, much rain"),
        (1030, "rising", "Settled fine"),
        (1000, "rising", "Early showers, improving"),
        (947, "rising", "Stormy, much rain"),
    ],
)
def test_forecast_documented_formula_table_pairs(pressure, trend, expected):
    assert ZambrettiForecaster().get_forecast(pressure, trend) == "Outlook: " + expected


@pytest.mark.parametrize("pressure,trend", [(None, "rising"), (1000, "-"), (1000, "bogus")])
def test_forecast_waits_for_valid_inputs(pressure, trend):
    assert ZambrettiForecaster().get_forecast(pressure, trend) == "Outlook: waiting for 3h"


@pytest.mark.parametrize(
    "pressure,trend", [(math.nan, "steady"), (984, "falling"), (1051, "falling"), (1034, "steady")]
)
def test_forecast_does_not_extrapolate(pressure, trend):
    assert ZambrettiForecaster().get_forecast(pressure, trend) == "Outlook: outside range"


def test_button_bounce_hold_and_release():
    buttons = ButtonTracker(count=1)
    assert buttons.update((True,), 0) == []
    assert buttons.update((False,), 0.01) == []
    assert buttons.update((True,), 0.02) == []
    assert buttons.update((True,), 0.08) == [0]
    assert buttons.update((True,), 600) == []
    assert buttons.update((False,), 601) == []
    assert buttons.update((False,), 602) == []
    assert buttons.update((True,), 603) == []
    assert buttons.update((True,), 604) == [0]


def test_schedule_skips_overdue_reads_and_stays_in_phase():
    schedule = SamplingSchedule(0)
    assert schedule.due(0)
    schedule.advance(600)
    assert schedule.next_due == 660
    assert not schedule.due(600)
    assert schedule.due(660)
    schedule.advance(660.5)
    assert schedule.next_due == 720


def test_history_uses_schedule_origin_despite_first_read_delay():
    history = DataHistory(10, start_time=0)
    add(history, 1, 0.1)
    add(history, 2, 60.01)
    add(history, 3, 120.01)
    assert history.data_count == 3
    assert history.value_ago(0, 2) == 1
