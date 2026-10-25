# SPDX-License-Identifier: MIT
"""Clock trust, health diagnostics, and watchdog control at the hardware boundary."""

import time


def trusted_epoch():
    """An unset CircuitPython RTC defaults to 2000; never treat that as UTC."""
    from weather_core import valid_number

    try:
        epoch = time.time()
        year = time.localtime().tm_year
        return (
            epoch if 2024 <= year <= 2099 and valid_number(epoch, 1704067200, 4102444800) else None
        )
    except (AttributeError, ValueError, OverflowError):
        return None


def latest_slot_epoch(history, now_monotonic, epoch_now):
    if epoch_now is None or history._origin is None or history._last_slot < 0:
        return None
    slot_time = history._origin + history._last_slot * history.interval_seconds
    return int(epoch_now) - int(round(now_monotonic - slot_time))


class WatchdogGuard:
    def __init__(self, enabled=True, timeout_seconds=30, editing=False, device=None, mode=None):
        self.enabled = False
        self.device = device
        self.status = "off: editing" if editing else "off"
        if not enabled or editing:
            return
        try:
            if self.device is None:
                import microcontroller
                import watchdog

                self.device = microcontroller.watchdog
                mode = watchdog.WatchDogMode.RESET
            if self.device is None:
                self.status = "unsupported"
                return
            self.device.timeout = timeout_seconds
            self.device.mode = mode
            self.enabled = True
            self.status = "armed {}s".format(timeout_seconds)
        except (ImportError, AttributeError, ValueError, RuntimeError) as error:
            self.status = "unavailable"
            print("Watchdog unavailable:", error)

    def feed(self):
        if self.enabled:
            self.device.feed()

    def stop(self):
        if not self.enabled:
            return True
        try:
            if hasattr(self.device, "deinit"):
                self.device.deinit()
            else:
                self.device.mode = None
            self.enabled = False
            self.status = "off"
            return True
        except (TypeError, ValueError, RuntimeError):
            return False


def reset_reason():
    try:
        import microcontroller

        return str(microcontroller.cpu.reset_reason).split(".")[-1]
    except (ImportError, AttributeError):
        return "unavailable"


def free_memory():
    import gc

    return gc.mem_free() if hasattr(gc, "mem_free") else None


def age_text(last_good, now):
    if last_good is None:
        return "never"
    seconds = max(0, int(now - last_good))
    return "{}s".format(seconds) if seconds < 60 else "{}m".format(seconds // 60)


class _CrashReport:
    """Keep a bounded excerpt of the formatted traceback."""

    def __init__(self):
        self.text = ""

    def write(self, text):
        self.text += text[: max(0, 2048 - len(self.text))]
        return len(text)


def record_crash(error, path="/last-crash.txt"):
    """Keep one bounded report; an identical recurring fault does not rewrite flash.

    Logging is best effort: a full, read-only or damaged filesystem must never
    prevent recovery. This file is separate from the alternating history slots.
    """
    try:
        import traceback

        report = _CrashReport()
        report.write(type(error).__name__ + ": " + str(error)[:240] + "\n")
        # CircuitPython 7's native print_exception requires a native I/O stream;
        # a Python object with write() is not a supported stream on this firmware.
        for line in traceback.format_exception(type(error), error, error.__traceback__, limit=8):
            report.write(line)
        print("WEATHER_CRASH\n" + report.text)
        data = report.text.encode("utf-8")[:2048]
        try:
            with open(path, "rb") as stream:
                if stream.read(2049) == data:
                    return True
        except OSError:
            pass
        with open(path, "wb") as stream:
            if stream.write(data) != len(data):
                raise OSError("short crash report write")
        import os

        sync = getattr(os, "sync", None)
        if sync is not None:
            sync()
        return True
    except Exception as logging_error:
        print("Weather crash report unavailable:", logging_error)
        return False


def _stop_active_watchdog():
    """Also allow Ctrl-C before WeatherStation construction has completed."""
    try:
        import microcontroller

        device = getattr(microcontroller, "watchdog", None)
        if device is not None:
            if hasattr(device, "deinit"):
                device.deinit()
            else:
                device.mode = None
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError):
        pass


def run_supervised(entry=None):
    """Restart after unexpected startup/runtime errors, including import failures.

    Ctrl-C and editing-mode errors stay at the REPL for repair. The hardware
    watchdog remains armed during fatal-error handling if it was already active.
    Imports belong inside the guard so startup is protected before it is armed.
    """
    try:
        if entry is None:
            from weather_station import main

            entry = main
        entry()
    except KeyboardInterrupt:
        _stop_active_watchdog()
        raise
    except Exception as error:
        try:
            try:
                from weather_boot import editing_mode

                editing = editing_mode()
            except Exception:
                editing = False
            if editing:
                raise
            record_crash(error)
            print("Weather station restarting in 5 seconds; Ctrl-C for REPL")
            import microcontroller

            time.sleep(5)
            microcontroller.reset()
        except KeyboardInterrupt:
            _stop_active_watchdog()
            raise
