# SPDX-License-Identifier: MIT
"""Replay sensor observations on a computer; never claims physical endurance.

Run from the project directory:
  python tools/replay_weather.py --days 21 --output replay-results.json
  python tools/replay_weather.py --csv readings.csv --output replay-results.json

CSV uses UTC Unix epoch seconds and columns epoch,pm25,pressure_hpa,
temperature_c,humidity. Pressure is already corrected to sea level. Blank,
NA, none, and nonfinite sensor values are treated as unavailable observations.
"""

import argparse
from collections import deque
import csv
import json
import math
from pathlib import Path
import random
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import weather_config  # noqa: E402
from weather_alerts import AlertManager  # noqa: E402
from weather_core import DataHistory, SamplingSchedule, ZambrettiForecaster  # noqa: E402
from weather_metrics import nowcast, pm_average, pressure_change  # noqa: E402
from weather_persistence import CheckpointStore  # noqa: E402
from weather_settings import validate_config  # noqa: E402


START_EPOCH = 1791504000  # 2026-10-09 00:00:00 UTC; deterministic UTC hour boundary.
CSV_FIELDS = ("epoch", "pm25", "pressure_hpa", "temperature_c", "humidity")


def synthetic_rows(days=21, seed=20261009):
    """Yield seeded minute readings with weekly condition spikes and faults."""
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise ValueError("Replay days must be a positive integer")
    rng = random.Random(seed)
    for slot in range(days * 24 * 60):
        elapsed = slot * 60
        week_hour = (elapsed % (7 * 86400)) / 3600.0
        # Five skipped reads model a stalled scheduler, without invented catch-up samples.
        if 72 <= week_hour < 72 + 5 / 60.0:
            continue
        day_angle = 2 * math.pi * elapsed / 86400.0
        pressure_angle = 2 * math.pi * elapsed / (48 * 3600.0)
        row = {
            "epoch": START_EPOCH + elapsed,
            "pm25": max(0, 8 + 3 * math.sin(day_angle) + rng.uniform(-1, 1)),
            "pressure_hpa": 1013 + 9 * math.sin(pressure_angle),
            "temperature_c": 18 + 6 * math.sin(day_angle),
            "humidity": 50 + 15 * math.cos(day_angle),
        }
        if 26 <= week_hour < 30:
            row["pm25"] = 80 + rng.uniform(-3, 3)
        # Three hours with all sensors unavailable also exercises NowCast coverage loss.
        if 49 <= week_hour < 52:
            for field in CSV_FIELDS[1:]:
                row[field] = None
        yield row


def csv_rows(path):
    """Read portable replay observations, with row-numbered parse failures."""
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or any(name not in reader.fieldnames for name in CSV_FIELDS):
            raise ValueError("CSV headers must include " + ",".join(CSV_FIELDS))
        for row_number, raw in enumerate(reader, 2):
            row = {}
            for name in CSV_FIELDS:
                value = raw[name]
                if value is None or value.strip().lower() in ("", "na", "none", "null"):
                    if name == "epoch":
                        raise ValueError("CSV row {} has no epoch".format(row_number))
                    row[name] = None
                    continue
                try:
                    row[name] = float(value)
                except ValueError:
                    raise ValueError("CSV row {} has invalid {}".format(row_number, name))
            yield row


def _measurement(value, lower, upper):
    if (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and lower <= value <= upper
    ):
        return value
    return None


def checkpoint_probe(history, directory, latest_epoch, latest_monotonic):
    """Recover a wrapped ring from corruption and a simulated incomplete write."""
    store = CheckpointStore(directory, history.max_samples, history.interval_seconds)
    preferences = {
        "graph_index": 2, "resolution_index": 1, "brightness_index": 3,
        "temperature_unit": "C",
    }
    assert store.save(history, preferences, latest_epoch, latest_monotonic)
    backup_generation = store._generation
    assert store.save(history, dict(preferences, temperature_unit="F"),
                      latest_epoch, latest_monotonic)
    newest_path = Path(store._current_path)
    with newest_path.open("r+b") as stream:
        stream.seek(-1, 2)
        last = stream.read(1)
        stream.seek(-1, 2)
        stream.write(bytes([last[0] ^ 0xFF]))
    recovery_epoch = latest_epoch + 95.25
    recovery_monotonic = 41.5
    restored = DataHistory(history.max_samples, history.interval_seconds)
    recovery = CheckpointStore(directory, history.max_samples, history.interval_seconds)
    result = recovery.load(restored, recovery_epoch, recovery_monotonic)
    assert result and result["clock_trusted"]
    assert result["generation"] == backup_generation
    assert result["preferences"] == preferences
    assert result["restored_samples"] + result["missing_slots"] == restored.data_count
    assert restored.storage_bytes == history.storage_bytes
    assert result["missing_slots"] >= 1
    assert restored.value_ago(0) is None
    assert restored.value_ago(0, result["missing_slots"]) == history.value_ago(0)

    # The newest broken slot now models a power loss in the middle of another write.
    newest_path.write_bytes(b"MWTHST1\n\xff")
    power_loss_history = DataHistory(history.max_samples, history.interval_seconds)
    power_loss_store = CheckpointStore(directory, history.max_samples, history.interval_seconds)
    power_loss = power_loss_store.load(power_loss_history, recovery_epoch, recovery_monotonic)
    assert power_loss and power_loss["generation"] == backup_generation
    assert power_loss["restored_samples"] == result["restored_samples"]

    # A current observation replaces its elapsed slot; the next scheduled phase appends.
    count_before = restored.data_count
    restored.add_reading(77, 1000, 68, 50, recovery_monotonic)
    assert restored.data_count == count_before
    assert restored.value_ago(0) == 77
    next_due = restored._origin + (restored._last_slot + 1) * restored.interval_seconds
    delay = next_due - recovery_monotonic
    assert 0 < delay <= history.interval_seconds
    restored.add_reading(78, 1001, 69, 51, next_due)
    assert restored.value_ago(0) == 78 and restored.value_ago(0, 1) == 77

    # A standalone board has no trustworthy UTC after power loss unless its RTC
    # was set. An older session remains browsable without becoming live data.
    archive_directory = str(Path(directory) / "unknown-clock")
    archive_store = CheckpointStore(
        archive_directory, history.max_samples, history.interval_seconds
    )
    assert archive_store.save(history, preferences, None, latest_monotonic)
    live = DataHistory(history.max_samples, history.interval_seconds)
    unknown_clock = archive_store.load(live, now_epoch=None, now_monotonic=5)
    assert unknown_clock and not unknown_clock["clock_trusted"]
    assert live.data_count == 0
    archive = DataHistory(history.max_samples, history.interval_seconds)
    archived = archive_store.load_archive(archive)
    assert archived and archived["archived"] and not archived["clock_trusted"]
    assert archive.data_count == history.data_count
    assert archive.value_ago(0) == history.value_ago(0)
    assert pressure_change(live) is None
    assert nowcast(live)["aqi"] is None
    session_intervals = int(math.ceil(7200 / history.interval_seconds))
    for slot in range(session_intervals + 1):
        live.add_reading(5, 1013, 68, 40, 5 + slot * history.interval_seconds)
    session_estimate = nowcast(
        live, session_intervals * history.interval_seconds, time_basis="session"
    )
    assert session_estimate["aqi"] is not None
    assert session_estimate["reason"] == "session estimate"
    assert session_estimate["concentration"] == 5
    return {
        "corrupted_latest_slot_fallback": True,
        "incomplete_write_fallback": True,
        "preferences_recovered": True,
        "missing_slots": result["missing_slots"],
        "restored_samples": result["restored_samples"],
        "clock_phase_preserved": True,
        "seconds_until_next_read": round(delay, 6),
        "ring_was_wrapped": history._last_slot >= history.max_samples,
        "unknown_clock_live_history_empty": True,
        "previous_session_archive_available": True,
        "archive_excluded_from_live_metrics": True,
        "standalone_nowcast_time_basis": "completed elapsed hours from current boot",
        "standalone_nowcast_reason": session_estimate["reason"],
    }


def run_replay(rows, checkpoint_directory, config=weather_config):
    """Return an inspectable report after asserting observable replay invariants."""
    errors = validate_config(config)
    if errors:
        raise ValueError("; ".join(errors))
    history = DataHistory(config.HISTORY_SAMPLES, config.READING_INTERVAL_SECONDS, 0)
    schedule = SamplingSchedule(0, config.READING_INTERVAL_SECONDS)
    alerts = AlertManager(config)
    forecaster = ZambrettiForecaster()
    expected = deque(maxlen=history.max_samples)
    sample_count = input_count = 0
    first_epoch = latest_epoch = previous_epoch = None
    previous_slot = -1
    previous_active = ()
    activations = {"PM high": 0, "Pressure change": 0}
    nowcast_checks = valid_nowcasts = unavailable_forecasts = 0
    outlook = "Outlook: waiting for 3h"
    estimate = {"aqi": None, "reason": "need 2 of last 3 hours"}
    for row in rows:
        input_count += 1
        epoch = row["epoch"]
        if _measurement(epoch, 1577836800, 4102444800) is None:
            raise ValueError("Every replay epoch must be finite UTC between 2020 and 2100")
        if previous_epoch is not None and epoch < previous_epoch:
            raise ValueError("Replay epochs must not go backwards")
        previous_epoch = epoch
        if first_epoch is None:
            first_epoch = epoch
        elapsed = epoch - first_epoch
        if not schedule.due(elapsed):
            continue
        schedule.advance(elapsed)
        pm = _measurement(row["pm25"], 0, 3276.7)
        pressure = _measurement(row["pressure_hpa"], 300, 1100)
        temp_c = _measurement(row["temperature_c"], -40, 85)
        temp_f = None if temp_c is None else temp_c * 1.8 + 32
        humidity = _measurement(row["humidity"], 0, 100)
        values = (pm, pressure, temp_f, humidity)
        slot = int(elapsed / history.interval_seconds + 1e-7)
        for _ in range(min(slot - previous_slot - 1, history.max_samples - 1)):
            expected.append((None, None, None, None))
        expected.append(values)
        previous_slot = slot
        history.add_reading(*values, timestamp=elapsed)
        sample_count += 1
        latest_epoch = epoch
        assert history.data_count == len(expected)
        assert history.storage_bytes == config.HISTORY_SAMPLES * 8
        delta = pressure_change(history, config.TREND_WINDOW_SECONDS, config.TREND_MAX_GAP_SECONDS)
        average = pm_average(history, config.PM_AVERAGE_SECONDS)
        active = alerts.update(average, delta, elapsed)
        for name in active:
            if name not in previous_active:
                activations[name] += 1
        previous_active = active
        trend = history.pressure_trend(
            config.TREND_WINDOW_SECONDS, config.TREND_THRESHOLD_HPA, config.TREND_MAX_GAP_SECONDS
        )
        outlook = forecaster.get_forecast(history.value_ago(1), trend)
        if delta is None:
            assert trend == "-"
            assert outlook == "Outlook: waiting for 3h"
            unavailable_forecasts += 1
        if slot % max(1, int(3600 / history.interval_seconds)) == 0:
            latest_slot_epoch = first_epoch + slot * history.interval_seconds
            estimate = nowcast(history, latest_slot_epoch, config.HOURLY_MINIMUM_COVERAGE)
            nowcast_checks += 1
            if estimate["aqi"] is not None:
                valid_nowcasts += 1
                assert estimate["valid_hours"] >= 2
    if sample_count == 0:
        raise ValueError("Replay needs at least one usable observation row")

    missing = {}
    for index, name in enumerate(CSV_FIELDS[1:]):
        expected_valid = sum(values[index] is not None for values in expected)
        actual_valid = history.summarize(index, history.max_samples, 1)[3]
        assert actual_valid == expected_valid
        missing[name] = len(expected) - expected_valid
    latest_slot_epoch = first_epoch + previous_slot * history.interval_seconds
    estimate = nowcast(history, latest_slot_epoch, config.HOURLY_MINIMUM_COVERAGE)
    recovery = checkpoint_probe(history, checkpoint_directory, latest_epoch, elapsed)
    return {
        "kind": "accelerated software replay",
        "physical_endurance_test": False,
        "limitation": (
            "Simulated time does not test sensor calibration, flash wear, or real uptime."
        ),
        "elapsed_days": round((latest_epoch - first_epoch) / 86400, 6),
        "input_rows": input_count,
        "sample_count": sample_count,
        "retained_slots": history.data_count,
        "history_capacity": history.max_samples,
        "expected_missing": missing,
        "nowcast_validity": {
            "time_basis": "completed UTC calendar hours from supplied observation epochs",
            "checks": nowcast_checks,
            "valid_checks": valid_nowcasts,
            "pending_checks": nowcast_checks - valid_nowcasts,
            "latest_aqi": estimate["aqi"],
            "latest_reason": estimate["reason"],
            "latest_valid_hours": estimate.get("valid_hours", 0),
        },
        "alerts_activated": activations,
        "forecast_unavailable_samples": unavailable_forecasts,
        "latest_forecast": outlook,
        "memory_payload_bytes": history.storage_bytes,
        "checkpoint_recovery": recovery,
        "invariants_passed": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--seed", type=int, default=20261009)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    rows = csv_rows(args.csv) if args.csv else synthetic_rows(args.days, args.seed)
    with tempfile.TemporaryDirectory(prefix="weather-replay-") as directory:
        report = run_replay(rows, directory)
    report["source"] = str(args.csv.resolve()) if args.csv else "seeded synthetic observations"
    if not args.csv:
        report["requested_days"] = args.days
        report["seed"] = args.seed
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
