# SPDX-License-Identifier: MIT
"""Clock trust, health diagnostics, and watchdog control at the hardware boundary."""

import time
from weather_core import valid_number


def trusted_epoch():
    """An unset CircuitPython RTC defaults to 2000; never treat that as UTC."""
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
