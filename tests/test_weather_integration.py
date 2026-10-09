"""Cross-module checks for trusted time, restart gaps, and derived estimates."""

from types import SimpleNamespace

import pytest

import weather_runtime
from weather_core import DataHistory
from weather_metrics import nowcast, pressure_change
from weather_persistence import CheckpointStore
from weather_runtime import latest_slot_epoch


EPOCH = 1800000000  # An exact UTC-hour boundary in the trusted epoch range.
PREFERENCES = {
    "graph_index": 2,
    "resolution_index": 3,
    "brightness_index": 0,
    "temperature_unit": "C",
}


def saved_history(tmp_path):
    history = DataHistory(1000, start_time=0)
    for slot in range(721):
        history.add_reading(10, 1000, 68, 50, slot * 60)
    store = CheckpointStore(str(tmp_path), max_samples=1000)
    assert store.save(history, PREFERENCES, EPOCH, now_monotonic=43200)
    return history, store


def test_restored_history_and_nowcast_preserve_fractional_restart_phase(tmp_path):
    original, store = saved_history(tmp_path)
    expected = nowcast(original, EPOCH)
    restored = DataHistory(1000)
    result = store.load(restored, now_epoch=EPOCH + 90, now_monotonic=10)
    assert result["restored_samples"] == 721
    assert result["missing_slots"] == 1
    assert result["preferences"] == PREFERENCES
    epoch = latest_slot_epoch(restored, 10, EPOCH + 90)
    assert epoch == EPOCH + 60
    assert nowcast(restored, epoch) == expected
    assert pressure_change(restored) is None  # Latest elapsed slot is missing.
    restored.add_reading(10, 1000, 68, 50, 10)
    assert restored.data_count == 722  # Replace current slot, never invent a burst.
    assert pressure_change(restored) == 0
    restored.add_reading(10, 1000, 68, 50, 70)
    assert latest_slot_epoch(restored, 70, EPOCH + 150) == EPOCH + 120
    assert nowcast(restored, EPOCH + 120) == expected


def test_restart_long_gap_suppresses_pressure_and_stale_nowcast(tmp_path):
    _, store = saved_history(tmp_path)
    restored = DataHistory(1000)
    downtime = 4 * 3600
    result = store.load(restored, now_epoch=EPOCH + downtime, now_monotonic=5)
    assert result["missing_slots"] == 240
    restored.add_reading(10, 1000, 68, 50, 5)
    assert pressure_change(restored) is None
    epoch = latest_slot_epoch(restored, 5, EPOCH + downtime)
    assert nowcast(restored, epoch)["aqi"] is None
    assert nowcast(restored, epoch)["reason"] == "need 2 of last 3 hours"


@pytest.mark.parametrize("epoch", [None, EPOCH - 1])
def test_untrusted_or_backwards_restart_only_restores_preferences(tmp_path, epoch):
    _, store = saved_history(tmp_path)
    restored = DataHistory(1000)
    result = store.load(restored, now_epoch=epoch, now_monotonic=10)
    assert result["preferences"] == PREFERENCES
    assert not result["clock_trusted"]
    assert restored.data_count == 0
    assert latest_slot_epoch(restored, 10, epoch) is None
    assert nowcast(restored, epoch)["aqi"] is None


def test_latest_slot_uses_schedule_time_even_when_sensor_io_and_ui_are_delayed():
    history = DataHistory(1000, start_time=17)
    history.add_reading(10, 1000, 68, 50, 17.8)
    history.add_reading(10, 1000, 68, 50, 80)
    assert history._last_slot == 1
    assert latest_slot_epoch(history, 90, EPOCH + 73) == EPOCH + 60
    assert latest_slot_epoch(history, 90, None) is None


def test_clock_unset_and_valid_device_unix_epoch(monkeypatch):
    monkeypatch.setattr(weather_runtime.time, "time", lambda: 946685148)
    monkeypatch.setattr(weather_runtime.time, "localtime", lambda: SimpleNamespace(tm_year=2000))
    assert weather_runtime.trusted_epoch() is None
    monkeypatch.setattr(weather_runtime.time, "time", lambda: EPOCH)
    monkeypatch.setattr(weather_runtime.time, "localtime", lambda: SimpleNamespace(tm_year=2027))
    assert weather_runtime.trusted_epoch() == EPOCH


def test_unknown_power_gap_keeps_archive_separate_from_live_session_estimates(tmp_path):
    _, store = saved_history(tmp_path)
    live = DataHistory(1000)
    result = store.load(live, now_epoch=None, now_monotonic=5)
    assert result["preferences"] == PREFERENCES
    archive = DataHistory(1000)
    archived = store.load_archive(archive)
    assert archived["archived"]
    assert archived["restored_samples"] == 721
    assert archive.data_count == 721
    assert live.data_count == 0
    for slot in range(61):
        live.add_reading(100, 1000, 68, 50, 5 + slot * 60)
    estimate = nowcast(live, 3600, time_basis="session")
    assert estimate["valid_hours"] == 1
    assert estimate["aqi"] is None
    assert pressure_change(live) is None
    assert archive.value_ago(0) == 10
    assert live.value_ago(0) == 100
    # A second complete hour establishes the new session independently.
    for slot in range(61, 121):
        live.add_reading(100, 1000, 68, 50, 5 + slot * 60)
    estimate = nowcast(live, 7200, time_basis="session")
    assert estimate["concentration"] == 100
    assert estimate["valid_hours"] == 2
    assert estimate["time_basis"] == "session"
