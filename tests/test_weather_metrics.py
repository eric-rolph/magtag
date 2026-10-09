import math
import struct
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

import pytest

import weather_metrics
from weather_core import DataHistory, pm_category
from weather_metrics import (
    concentration_to_aqi,
    dew_point_c,
    nowcast,
    pm_average,
    pressure_change,
    temperature_from_f,
)


def readings(values, interval=60, capacity=None, series=0):
    history = DataHistory(capacity or len(values), interval)
    for index, value in enumerate(values):
        channels = [None] * 4
        channels[series] = value
        history.add_reading(*channels, timestamp=index * interval)
    return history


def hourly_history(newest_first, latest_epoch=46800, partial_value=1000, interval=60, phase=0):
    """Expand test hours into cadence readings independently of production bins."""
    end = latest_epoch // 3600 * 3600
    start_epoch = end - 43200 + phase
    history = DataHistory(1000, interval)
    for index in range(int((latest_epoch - start_epoch) // interval) + 1):
        epoch = start_epoch + index * interval
        age = int(end // 3600 - 1 - epoch // 3600)
        value = partial_value if age < 0 else newest_first[age]
        history.add_reading(value, 1000, 68, 50, index * interval)
    actual_latest = start_epoch + (history.data_count - 1) * interval
    return history, actual_latest


def decimal_nowcast(hourly):
    """Independent exact decimal reference for expected test calculations."""
    values = [Decimal(str(value)) for value in hourly if value is not None]
    weight = max(Decimal("0.5"), min(values) / max(values)) if max(values) else Decimal(1)
    numerator = denominator = Decimal(0)
    for age, value in enumerate(hourly):
        if value is not None:
            numerator += Decimal(str(value)) * weight ** age
            denominator += weight ** age
    return (numerator / denominator).quantize(Decimal("0.1"), rounding=ROUND_DOWN)


@pytest.mark.parametrize("temperature", [-40, 0, 20, 50, 85])
def test_saturation_dew_point_equals_air_temperature(temperature):
    assert dew_point_c(temperature, 100) == pytest.approx(temperature)


def test_dew_point_known_conditions_and_humidity_effect():
    assert dew_point_c(20, 50) == pytest.approx(9.2611, abs=0.0001)
    assert dew_point_c(25, 60) == pytest.approx(16.6977, abs=0.0001)
    assert dew_point_c(20, 20) < dew_point_c(20, 50) < dew_point_c(20, 90) < 20
    assert dew_point_c(-40, 1) < -40


@pytest.mark.parametrize(
    "temperature,humidity",
    [(20, 0), (20, -1), (20, 101), (None, 50), (20, None), (math.nan, 50),
     (20, math.inf), (20, "50"), (20, True), (-41, 50), (86, 50)],
)
def test_invalid_dew_point_inputs_return_none(temperature, humidity):
    assert dew_point_c(temperature, humidity) is None


def test_display_temperature_conversion():
    assert temperature_from_f(68) == 68
    assert temperature_from_f(68, "C") == pytest.approx(20)
    assert temperature_from_f(-40, "C") == -40
    assert temperature_from_f(None, "C") is None
    assert temperature_from_f(math.inf) is None
    assert temperature_from_f(True) is None
    with pytest.raises(ValueError, match="F or C"):
        temperature_from_f(68, "K")


def test_pm_five_minute_average_excludes_older_values_and_rejects_gaps():
    assert pm_average(readings([100, 1, 2, 3, 4, 5])) == 3
    assert pm_average(readings([1, None, 3, 4, 5])) == 3.25
    assert pm_average(readings([1, None, None, 4, 5])) is None
    assert pm_average(readings([0] * 5)) == 0
    assert pm_average(readings([1] * 3)) is None
    assert pm_average(readings([1] * 4)) == 1
    assert pm_average(readings([-1] * 5)) is None


def test_pm_average_changes_with_interval_and_coverage():
    history = readings([2, 4, None], interval=120)
    assert pm_average(history) is None
    assert pm_average(history, minimum_coverage=0.6) == 3
    with pytest.raises(ValueError):
        pm_average(history, minimum_coverage=0)
    with pytest.raises(ValueError):
        pm_average(history, seconds=0)


@pytest.mark.parametrize("value", [9.1, 35.5, 35.9, 55.5])
def test_pm_average_constant_decimal_uses_encoded_integer_tenths(value):
    assert pm_average(readings([value] * 5)) == value


@pytest.mark.parametrize("delta", [-1.6, 0, 1.6])
def test_pressure_change_matches_trend_endpoint_eligibility(delta):
    values = [1000 + delta * index / 180 for index in range(181)]
    assert pressure_change(readings(values[:-1], series=1)) is None
    history = readings(values, series=1)
    assert pressure_change(history) == pytest.approx(delta)
    assert history.pressure_trend() != "-"


def test_pressure_change_gap_and_coverage_rules():
    values = [1000] * 181
    values[30:35] = [None] * 5
    history = readings(values, series=1)
    assert pressure_change(history) == 0
    values[35] = None
    history = readings(values, series=1)
    assert pressure_change(history) is None
    assert history.pressure_trend() == "-"
    values = [1000 if index % 4 else None for index in range(181)]
    values[0] = values[-1] = 1000
    history = readings(values, series=1)
    assert pressure_change(history) is None
    assert history.pressure_trend() == "-"
    values = [1000] * 181
    for endpoint in (0, -1):
        damaged = list(values)
        damaged[endpoint] = None
        assert pressure_change(readings(damaged, series=1)) is None


@pytest.mark.parametrize(
    "concentration,expected",
    [(0, 0), (0.09, 0), (4.5, 25), (7.1, 39), (7.3, 41),
     (9.0, 50), (9.099, 50), (9.1, 51), (35.4, 100), (35.499, 100),
     (35.5, 101), (55.4, 150), (55.5, 151), (125.4, 200), (125.5, 201),
     (225.4, 300), (225.5, 301), (325.4, 500), (325.499, 500), (325.5, 501),
     (500.4, 849)],
)
def test_current_epa_breakpoints_truncation_rounding_and_extrapolation(concentration, expected):
    assert concentration_to_aqi(concentration) == expected


@pytest.mark.parametrize("value", [None, -0.1, math.nan, math.inf, "35.9", True, 3276.8])
def test_invalid_concentrations_have_no_aqi(value):
    assert concentration_to_aqi(value) is None


def test_epa_published_pm_aqi_example_independently_computed():
    # EPA May 2026 AQI Technical Assistance Document p. 15: 35.9 -> 102.
    expected = (Decimal(49) / Decimal("19.9") * Decimal("0.4") + Decimal(101))
    expected = int(expected.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    assert expected == 102
    assert concentration_to_aqi(35.999) == expected


@pytest.mark.parametrize("value,expected_aqi", [(0, 0), (10, 53), (35.9, 102), (400, 649)])
def test_nowcast_constant_and_zero_hours(value, expected_aqi):
    history, epoch = hourly_history([value] * 12)
    result = nowcast(history, epoch)
    assert result["concentration"] == value
    assert result["aqi"] == expected_aqi
    assert result["valid_hours"] == 12
    assert (result["category"], result["color"]) == pm_category(value)
    assert result["reason"] == ("above-range estimate" if value > 325.4 else "sensor estimate")


@pytest.mark.parametrize("epoch", [None, -1, math.nan, math.inf, True])
def test_nowcast_requires_trusted_utc_clock(epoch):
    result = nowcast(readings([10] * 100), epoch)
    assert result["aqi"] is None
    assert result["concentration"] is None
    assert result["reason"] == "pending clock"


def test_nowcast_requires_two_of_latest_three_but_not_twelve_hours():
    history, epoch = hourly_history([None, None, 10] + [10] * 9)
    result = nowcast(history, epoch)
    assert result["valid_hours"] == 10
    assert result["aqi"] is None
    history, epoch = hourly_history([10, None, 10] + [None] * 9)
    assert nowcast(history, epoch)["aqi"] == 53
    history, epoch = hourly_history([None, 10, 10] + [None] * 9)
    assert nowcast(history, epoch)["aqi"] == 53


def test_nowcast_hourly_coverage_exact_threshold_and_missing_denominator():
    history, epoch = hourly_history([10] * 12)
    # Latest completed hour occupies ago 1..60. Exactly 45/60 is eligible.
    for ago in range(1, 16):
        history._series[0][(history.write_index - 1 - ago) % history.max_samples] = history.MISSING
    assert nowcast(history, epoch)["valid_hours"] == 12
    history._series[0][(history.write_index - 1 - 16) % history.max_samples] = history.MISSING
    assert nowcast(history, epoch)["valid_hours"] == 11
    with pytest.raises(ValueError):
        nowcast(history, epoch, hourly_coverage=1.1)


def test_nowcast_partial_current_hour_excluded_at_boundary_and_mid_hour():
    for latest in (46800, 46800 + 1800, 46800 + 3540):
        history, epoch = hourly_history([10] * 12, latest_epoch=latest, partial_value=1000)
        assert nowcast(history, epoch)["concentration"] == 10


def test_nowcast_utc_alignment_handles_slot_phase_and_nondividing_cadence():
    hourly = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]
    expected = float(decimal_nowcast(hourly))
    for interval, phase in ((60, 17), (90, 29), (1000, 17)):
        history, epoch = hourly_history(
            hourly, latest_epoch=46800 + 1800, interval=interval, phase=phase
        )
        result = nowcast(history, epoch)
        assert result["concentration"] == expected
        assert result["valid_hours"] == 12


@pytest.mark.parametrize("hourly", [
    [100] + [0] * 11,
    [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 20],
    [0, 10, None, 100] + [None] * 8,
    [None, 100, 0, 50] + [None] * 8,
])
def test_nowcast_changes_and_missing_hour_ages_against_decimal_reference(hourly):
    history, epoch = hourly_history(hourly)
    assert nowcast(history, epoch)["concentration"] == float(decimal_nowcast(hourly))


def test_epa_published_nowcast_example_independently_computed():
    # EPA/AirNow example hosted by NWS, slide 11:
    # https://www.weather.gov/media/sti/aq/presentations/20160915/
    # EPA%20and%20AIRNOW%20Activities/Dickerson_airnowffg2016.pdf
    # Original order is oldest->newest; published truncated result is 17.4.
    hourly = list(reversed([50, 80, 75, 90, 82, 53, 64, 74, 21, 10, 16, 13]))
    expected = float(decimal_nowcast(hourly))
    assert expected == 17.4
    history, epoch = hourly_history(hourly)
    assert nowcast(history, epoch)["concentration"] == expected
    assert nowcast(history, epoch)["aqi"] == 66


def test_airnow_published_missing_hour_example_independently_computed():
    # https://forum.airnowtech.org/t/the-nowcast-for-pm-2-5-and-pm10/172
    hourly = [21, None, 35, 49.2, 48.6, 53.7, 66.2, 69.2, 64.9, 50, 43, 34.9]
    expected = float(decimal_nowcast(hourly))
    assert expected == 28.4
    history, epoch = hourly_history(hourly)
    result = nowcast(history, epoch)
    assert result["concentration"] == expected
    assert result["aqi"] == 87
    assert result["valid_hours"] == 11


def test_nowcast_does_not_round_hourly_means_before_weighting():
    history, epoch = hourly_history([10] * 12)
    # Newest completed hourly average becomes 10.05, which must retain precision.
    for ago in range(1, 31):
        history._series[0][(history.write_index - 1 - ago) % history.max_samples] = 101
    for ago in range(61, 121):
        history._series[0][(history.write_index - 1 - ago) % history.max_samples] = 103
    expected = float(decimal_nowcast([10.05, 10.3] + [10] * 10))
    assert nowcast(history, epoch)["concentration"] == expected


def test_empty_and_short_history_are_pending():
    history = DataHistory(1000)
    assert nowcast(history, 46800)["aqi"] is None
    for index in range(44):
        history.add_reading(10, 1000, 68, 50, index * 60)
    result = nowcast(history, 46800)
    assert result["valid_hours"] == 0
    assert result["aqi"] is None


def test_session_nowcast_uses_completed_hours_since_boot_and_ignores_prior_session():
    # Buffer includes ten older hours from before this boot. Session mode must
    # omit them even when a caller retained an old buffer during a reset.
    hourly = [20, 20] + [100] * 10
    history, elapsed = hourly_history(hourly, latest_epoch=7200, partial_value=1000)
    result = nowcast(history, elapsed, time_basis="session")
    assert result["concentration"] == 20
    assert result["aqi"] == 71
    assert result["valid_hours"] == 2
    assert result["reason"] == "session estimate"
    assert result["time_basis"] == "session"
    assert nowcast(history, elapsed)["concentration"] != 20


def test_session_nowcast_needs_two_completed_boot_hours_even_with_old_history():
    history, elapsed = hourly_history([10] * 12, latest_epoch=3600, partial_value=1000)
    result = nowcast(history, elapsed, time_basis="session")
    assert result["valid_hours"] == 1
    assert result["aqi"] is None
    assert result["time_basis"] == "session"


def test_session_boundary_phase_is_independent_of_wall_clock():
    # A boot at 00:30 UTC uses elapsed hour boundaries at UTC 01:30, 02:30...
    history = DataHistory(200)
    for slot in range(151):
        value = 10 if slot < 60 else 30 if slot < 120 else 1000
        history.add_reading(value, 1000, 68, 50, slot * 60)
    session = nowcast(history, 9000, time_basis="session")
    assert session["concentration"] == 23.3
    assert session["valid_hours"] == 2
    utc = nowcast(history, 10800, time_basis="utc")
    assert utc["concentration"] != session["concentration"]


def test_session_estimate_labels_above_range_and_rejects_unknown_time_basis():
    history, elapsed = hourly_history([400] * 12, latest_epoch=7200)
    result = nowcast(history, elapsed, time_basis="session")
    assert result["reason"] == "above-range session estimate"
    assert nowcast(history, None, time_basis="session")["reason"] == "pending clock"
    with pytest.raises(ValueError, match="utc or session"):
        nowcast(history, elapsed, time_basis="local")


@pytest.mark.parametrize("value,expected", [(9.1, 51), (35.5, 101), (35.9, 102), (55.5, 151)])
def test_circuitpython_float_boundary_representation_is_corrected(monkeypatch, value, expected):
    # CP7 REPR_C stores IEEE32 floats with the lowest two bits cleared.
    bits = struct.unpack("<I", struct.pack("<f", value))[0] & ~3
    represented = struct.unpack("<f", struct.pack("<I", bits))[0]
    monkeypatch.setattr(weather_metrics, "_FLOAT_EPSILON", 1e-6)
    assert concentration_to_aqi(represented) == expected
    assert concentration_to_aqi(9.099) == 50
    assert concentration_to_aqi(35.499) == 100


def test_positive_coverage_contract_accepts_values_validated_by_settings():
    history, epoch = hourly_history([10] * 12)
    assert nowcast(history, epoch, hourly_coverage=0.005)["aqi"] == 53
    assert pm_average(readings([10] * 5), minimum_coverage=0.005) == 10
    with pytest.raises(ValueError):
        nowcast(history, epoch, hourly_coverage=0)


@pytest.mark.parametrize("value,expected", [(9.1, 51), (35.5, 101), (35.9, 102), (55.5, 151)])
def test_nowcast_integer_accumulation_preserves_constant_boundary(value, expected):
    history, epoch = hourly_history([value] * 12)
    result = nowcast(history, epoch)
    assert result["concentration"] == value
    assert result["aqi"] == expected
