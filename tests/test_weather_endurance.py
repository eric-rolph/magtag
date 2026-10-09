from types import SimpleNamespace

import pytest

import weather_config
from weather_alerts import SustainedAlert
from weather_core import DataHistory, ZambrettiForecaster
from weather_metrics import pressure_change
from weather_persistence import CheckpointStore
from tools.replay_weather import checkpoint_probe, csv_rows, run_replay, synthetic_rows


def test_seeded_three_week_replay_wraps_week_buffer_and_recovers_checkpoints(tmp_path):
    report = run_replay(synthetic_rows(21), str(tmp_path))
    assert report["sample_count"] == 21 * 24 * 60 - 15
    assert report["retained_slots"] == weather_config.HISTORY_SAMPLES
    assert report["memory_payload_bytes"] == 80640
    assert report["expected_missing"]["pm25"] == 185
    assert report["expected_missing"]["pressure_hpa"] == 185
    assert report["alerts_activated"]["PM high"] == 3
    assert report["alerts_activated"]["Pressure change"] > 0
    assert report["nowcast_validity"]["valid_checks"] > 400
    assert report["nowcast_validity"]["pending_checks"] > 2
    assert report["checkpoint_recovery"]["ring_was_wrapped"]
    assert report["checkpoint_recovery"]["clock_phase_preserved"]
    assert report["checkpoint_recovery"]["unknown_clock_live_history_empty"]
    assert report["checkpoint_recovery"]["previous_session_archive_available"]
    assert report["checkpoint_recovery"]["archive_excluded_from_live_metrics"]
    assert report["checkpoint_recovery"]["standalone_nowcast_reason"] == "session estimate"
    assert report["physical_endurance_test"] is False
    assert report["invariants_passed"]


def test_forecast_becomes_unavailable_across_outage_and_recovers_after_three_hours():
    history = DataHistory(400, start_time=0)
    forecaster = ZambrettiForecaster()
    for slot in range(181):
        history.add_reading(5, 1013 + slot / 100, 70, 40, slot * 60)
    assert pressure_change(history) == pytest.approx(1.8)
    outlook = forecaster.get_forecast(history.value_ago(1), history.pressure_trend())
    assert not outlook.endswith("3h")
    for slot in range(181, 191):
        history.add_reading(None, None, None, None, slot * 60)
    assert pressure_change(history) is None
    assert forecaster.get_forecast(history.value_ago(1), history.pressure_trend()).endswith("3h")
    for slot in range(191, 371):
        history.add_reading(5, 1015, 70, 40, slot * 60)
    assert pressure_change(history) is None  # Oldest endpoint still unavailable.
    history.add_reading(5, 1015, 70, 40, 371 * 60)
    assert pressure_change(history) == 0
    assert history.pressure_trend() == "steady"
    assert "waiting" not in forecaster.get_forecast(1015, history.pressure_trend())


def test_missing_and_scheduler_gaps_cannot_complete_an_alert_duration():
    alert = SustainedAlert(35.5, 30, 300)
    for now in (0, 60, 120, 180, 240):
        assert not alert.update(80, now)
    assert not alert.update(None, 300)
    for now in (360, 420, 480, 540, 600):
        assert not alert.update(80, now)
    assert not alert.update(80, 900)  # Stalled scheduler resets pending duration.
    for now in (960, 1020, 1080, 1140):
        assert not alert.update(80, now)
    assert alert.update(80, 1200)


def test_checkpoint_after_ring_wrap_continues_fractional_clock_phase(tmp_path):
    history = DataHistory(181, start_time=0)
    for slot in range(400):
        history.add_reading(slot % 100, 1013, 70, 40, slot * 60)
    epoch_of_latest_slot = 1791504000 + 399 * 60
    saved_epoch = epoch_of_latest_slot + 17.5
    store = CheckpointStore(str(tmp_path), 181, 60)
    assert store.save(history, {}, saved_epoch, 399 * 60 + 17.5)
    restored = DataHistory(181)
    boot_monotonic = 23.25
    result = store.load(restored, saved_epoch + 77, boot_monotonic)
    assert result["missing_slots"] == 1
    assert restored.value_ago(0) is None
    assert restored.value_ago(0, 1) == 99
    restored.add_reading(100, 1013, 70, 40, boot_monotonic)
    next_due = restored._origin + (restored._last_slot + 1) * 60
    assert next_due - boot_monotonic == pytest.approx(25.5)
    restored.add_reading(101, 1013, 70, 40, next_due)
    assert [restored.value_ago(0, ago) for ago in range(3)] == [101, 100, 99]


def test_replay_csv_schedules_reads_and_preserves_missing_observations(tmp_path):
    csv_path = tmp_path / "readings.csv"
    csv_path.write_text(
        "epoch,pm25,pressure_hpa,temperature_c,humidity\n"
        "1791504000,8,1013,20,40\n"
        "1791504010,999,1013,20,40\n"  # Earlier than the next scheduled sample.
        "1791504060,,1013,20,40\n"
        "1791504180,10,1013,20,40\n", encoding="utf-8",
    )
    config = SimpleNamespace(**{
        name: getattr(weather_config, name) for name in dir(weather_config) if name.isupper()
    })
    config.HISTORY_SAMPLES = 181
    config.RESOLUTION_HOURS = (1, 2, 3)
    config.DEFAULT_GRAPH_HOURS = 3
    report = run_replay(csv_rows(csv_path), str(tmp_path / "state"), config)
    assert report["input_rows"] == 4
    assert report["sample_count"] == 3
    assert report["retained_slots"] == 4
    assert report["expected_missing"]["pm25"] == 2
    assert report["expected_missing"]["pressure_hpa"] == 1


def test_csv_requires_clear_headers_and_parseable_epochs(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("time,pm\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="headers"):
        list(csv_rows(csv_path))
    csv_path.write_text(
        "epoch,pm25,pressure_hpa,temperature_c,humidity\n,2,1013,20,40\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="row 2"):
        list(csv_rows(csv_path))


def test_replay_detects_backward_time_and_empty_input(tmp_path):
    rows = list(synthetic_rows(1))[:2]
    rows[1]["epoch"] = rows[0]["epoch"] - 1
    with pytest.raises(ValueError, match="backwards"):
        run_replay(rows, str(tmp_path))
    with pytest.raises(ValueError, match="at least one"):
        run_replay([], str(tmp_path))


def test_checkpoint_probe_rejects_losing_the_only_valid_snapshot(tmp_path, monkeypatch):
    history = DataHistory(181, start_time=0)
    history.add_reading(5, 1013, 70, 40, 0)
    monkeypatch.setattr(CheckpointStore, "load", lambda *_args, **_kwargs: None)
    with pytest.raises(AssertionError):
        checkpoint_probe(history, str(tmp_path), 1791504000, 0)
