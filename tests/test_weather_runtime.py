from types import SimpleNamespace

import pytest
import weather_runtime as runtime
from weather_core import DataHistory


@pytest.mark.parametrize(
    "year,epoch,expected",
    [(2000, 946684800, None), (2026, 1791568800, 1791568800), (2100, 4102444800, None)],
)
def test_clock_requires_valid_calendar_and_epoch(monkeypatch, year, epoch, expected):
    monkeypatch.setattr(runtime.time, "time", lambda: epoch)
    monkeypatch.setattr(runtime.time, "localtime", lambda: SimpleNamespace(tm_year=year))
    assert runtime.trusted_epoch() == expected


def test_latest_slot_epoch_preserves_integer_utc_and_sampling_phase():
    history = DataHistory(10, start_time=3.5)
    history.add_reading(1, 1000, 70, 40, 63.5)
    assert runtime.latest_slot_epoch(history, 67.5, 1791568807) == 1791568803
    assert runtime.latest_slot_epoch(history, 67.5, None) is None


def test_watchdog_is_disabled_in_editing_mode_and_deinitializes_for_repl():
    class Device:
        def __init__(self):
            self.calls = []

        def feed(self):
            self.calls.append("feed")

        def deinit(self):
            self.calls.append("stop")

    device = Device()
    editing = runtime.WatchdogGuard(editing=True, device=device, mode="RESET")
    editing.feed()
    assert not editing.enabled and device.calls == []
    guard = runtime.WatchdogGuard(device=device, mode="RESET")
    assert device.timeout == 30 and device.mode == "RESET"
    guard.feed()
    assert guard.stop()
    guard.feed()
    assert device.calls == ["feed", "stop"]
