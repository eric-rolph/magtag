from types import SimpleNamespace
import sys

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


@pytest.mark.parametrize("failure", [ImportError("missing driver"), RuntimeError("tick failed")])
def test_unexpected_startup_and_runtime_errors_record_then_reset(monkeypatch, failure):
    calls = []
    monkeypatch.setitem(sys.modules, "microcontroller", SimpleNamespace(
        reset=lambda: calls.append("reset"),
    ))
    monkeypatch.setitem(sys.modules, "weather_boot", SimpleNamespace(editing_mode=lambda: False))
    monkeypatch.setattr(runtime, "record_crash", lambda error: calls.append(error))
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: calls.append(seconds))

    def entry():
        raise failure

    runtime.run_supervised(entry)
    assert calls == [failure, 5, "reset"]


def test_application_import_failure_is_inside_recovery_guard(monkeypatch):
    import builtins

    failure = ImportError("weather_station unavailable")
    original_import = builtins.__import__
    calls = []

    def import_with_fault(name, *args, **kwargs):
        if name == "weather_station":
            raise failure
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_with_fault)
    monkeypatch.setitem(sys.modules, "weather_boot", SimpleNamespace(editing_mode=lambda: False))
    monkeypatch.setitem(sys.modules, "microcontroller", SimpleNamespace(
        reset=lambda: calls.append("reset"),
    ))
    monkeypatch.setattr(runtime, "record_crash", lambda error: calls.append(error))
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
    runtime.run_supervised()
    assert calls == [failure, "reset"]


@pytest.mark.parametrize("editing,failure", [(False, KeyboardInterrupt()),
                                            (True, RuntimeError("editing failure"))])
def test_editing_and_ctrl_c_leave_device_at_repl(monkeypatch, editing, failure):
    monkeypatch.setitem(sys.modules, "weather_boot", SimpleNamespace(editing_mode=lambda: editing))
    monkeypatch.setattr(runtime, "record_crash", lambda _: pytest.fail("Must not write"))
    monkeypatch.setattr(runtime.time, "sleep", lambda _: pytest.fail("Must not restart"))

    def entry():
        raise failure

    with pytest.raises(type(failure)):
        runtime.run_supervised(entry)


def test_crash_report_is_bounded_deduplicated_and_separate_from_history(tmp_path):
    report = tmp_path / "last-crash.txt"
    error = RuntimeError("sensor fault " + "x" * 10000)
    assert runtime.record_crash(error, str(report))
    assert report.stat().st_size <= 2048
    modified = report.stat().st_mtime_ns
    assert runtime.record_crash(error, str(report))
    assert report.stat().st_mtime_ns == modified
    assert report.read_text().startswith("RuntimeError: sensor fault")


def test_crash_report_full_readonly_or_missing_storage_cannot_block_recovery(tmp_path):
    assert not runtime.record_crash(RuntimeError("read failed"), str(tmp_path / "absent" / "log"))


def test_crash_traceback_keeps_original_function_and_exception(tmp_path):
    report = tmp_path / "last-crash.txt"
    try:
        raise ValueError("fault injection")
    except ValueError as error:
        assert runtime.record_crash(error, str(report))
    text = report.read_text()
    assert "test_crash_traceback_keeps_original_function_and_exception" in text
    assert "ValueError: fault injection" in text


def test_ctrl_c_during_recovery_disarms_watchdog_before_repl(monkeypatch):
    device = SimpleNamespace(mode="RESET")
    monkeypatch.setitem(sys.modules, "microcontroller", SimpleNamespace(
        watchdog=device, reset=lambda: pytest.fail("Must not reset"),
    ))
    monkeypatch.setitem(sys.modules, "weather_boot", SimpleNamespace(editing_mode=lambda: False))
    monkeypatch.setattr(runtime, "record_crash", lambda _: True)

    def interrupted_sleep(_):
        raise KeyboardInterrupt()

    def failure():
        raise RuntimeError("fault")

    monkeypatch.setattr(runtime.time, "sleep", interrupted_sleep)
    with pytest.raises(KeyboardInterrupt):
        runtime.run_supervised(failure)
    assert device.mode is None


def test_missing_boot_helper_cannot_disable_startup_recovery(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "weather_boot", None)
    monkeypatch.setitem(sys.modules, "microcontroller", SimpleNamespace(
        reset=lambda: calls.append("reset"),
    ))
    monkeypatch.setattr(runtime, "record_crash", lambda _: True)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)

    def failure():
        raise ImportError("missing weather_boot")

    runtime.run_supervised(failure)
    assert calls == ["reset"]


def test_interrupt_during_partial_startup_disarms_watchdog(monkeypatch):
    device = SimpleNamespace(mode="RESET")
    monkeypatch.setitem(sys.modules, "microcontroller", SimpleNamespace(watchdog=device))

    def interrupted_startup():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        runtime.run_supervised(interrupted_startup)
    assert device.mode is None


def test_interrupt_while_recording_crash_disarms_watchdog(monkeypatch):
    device = SimpleNamespace(mode="RESET")
    monkeypatch.setitem(sys.modules, "microcontroller", SimpleNamespace(watchdog=device))
    monkeypatch.setitem(sys.modules, "weather_boot", SimpleNamespace(editing_mode=lambda: False))

    def interrupted_report(_):
        raise KeyboardInterrupt()

    def failure():
        raise RuntimeError("fault")

    monkeypatch.setattr(runtime, "record_crash", interrupted_report)
    with pytest.raises(KeyboardInterrupt):
        runtime.run_supervised(failure)
    assert device.mode is None
