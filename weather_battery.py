# SPDX-License-Identifier: MIT
"""Conservative, session-only estimates from LOAD OUT; not a fuel gauge."""

import math


def usb_power():
    """Unknown USB state must never enable a battery estimate."""
    try:
        import supervisor

        return bool(supervisor.runtime.usb_connected)
    except (ImportError, AttributeError, RuntimeError):
        return None


class BatteryEstimate:
    def __init__(self, capacity_mah, curve):
        self.capacity_mah = capacity_mah
        self.curve = curve
        self.voltage = None
        self.percent = None
        self.hours = None
        self._active = False
        self.state = "unknown"
        self._points = []
        self._last = None
        self._low = None
        self._load = None
        self._settle_until = 0
        self._candidate_since = None
        self._rise_peak = None

    def clear(self, state="unknown"):
        self._active = False
        self.percent = self.hours = None
        self._points = []
        self._low = None
        self.state = state
        self._candidate_since = None
        self._rise_peak = None

    def _percentage(self, voltage):
        if voltage <= self.curve[0][0]:
            return 0.0
        for index in range(1, len(self.curve)):
            low_v, low_p = self.curve[index - 1]
            high_v, high_p = self.curve[index]
            if voltage <= high_v:
                return low_p + (high_p - low_p) * (voltage - low_v) / (high_v - low_v)
        return 100.0

    def update(self, now, voltage, usb, load=None):
        valid = (isinstance(voltage, (float, int)) and not isinstance(voltage, bool)
                 and math.isfinite(voltage) and 2.5 <= voltage <= 4.6)
        self.voltage = voltage if valid else None
        if self._last is not None and (now < self._last or now - self._last > 120):
            self.clear()
        self._last = now
        if not valid:
            self.clear("unavailable")
            return
        elif usb is not False:
            self.clear("usb" if usb else "unknown")
            return
        elif voltage > 4.22:
            self.clear("external")
            return
        elif voltage < 3.0:
            self.clear("low")
            return
        elif self._low is not None and voltage > self._low + 0.06:
            self.clear("rising")
            self._rise_peak = voltage
        if self._rise_peak is not None:
            self._rise_peak = max(self._rise_peak, voltage)
            if voltage > self._rise_peak - 0.02:
                return  # Wait for discharge evidence after apparent charging/rebound.
            self.clear()
        if not self._active:
            self.state = "checking"
            if self._candidate_since is None:
                self._candidate_since = now
                self._low = voltage
            if now - self._candidate_since < 20:
                return
            self._active = True
            self._settle_until = now + 300
        self.state = "battery"
        self._low = voltage if self._low is None else min(self._low, voltage)
        self.percent = self._percentage(voltage)
        if load != self._load:
            self._points = []
            self.hours = None
            self._settle_until = now + 300
            self._load = load
        if now < self._settle_until:
            return
        if self._points and now - self._points[-1][0] < 300:
            return
        self._points.append((now, self.percent))
        self._points = self._points[-49:]  # At most four hours, independent of uptime.
        self.hours = None
        if len(self._points) < 25:
            return
        start, end = self._points[0], self._points[-1]
        middle = self._points[len(self._points) // 2]
        drop = start[1] - end[1]
        if end[0] - start[0] < 7200 or drop < 10:
            return
        first_rate = (start[1] - middle[1]) / (middle[0] - start[0])
        last_rate = (middle[1] - end[1]) / (end[0] - middle[0])
        if first_rate <= 0 or not 0.5 <= last_rate / first_rate <= 2:
            return
        hours = end[1] / (drop / ((end[0] - start[0]) / 3600))
        if 0 <= hours <= 48:
            self.hours = hours

    def text(self):
        volts = "{:.2f}V".format(self.voltage) if self.voltage is not None else "--V"
        if self.percent is None:
            captions = {"usb": "USB", "external": "EXT", "unavailable": "BAT --",
                        "unknown": "PWR ?", "rising": "PWR ?", "checking": "AUTO",
                        "low": "LOW"}
            reasons = {"usb": "Charge unknown", "external": "Charge unknown",
                       "unavailable": "Reading unavailable", "unknown": "Source unknown",
                       "rising": "Voltage rising", "checking": "Checking power",
                       "low": "Battery voltage low"}
            return (captions[self.state], captions[self.state] + " " + volts
                    + " | " + reasons[self.state])
        percent = int(self.percent / 5 + 0.5) * 5
        remaining = "Learning" if self.hours is None else "~{:.0f}h".format(self.hours)
        if self.hours is not None and self.hours < 1:
            remaining = "<1h"
        return "~{}%".format(percent), "Bat ~{}% {} | Left {}".format(
            percent, volts, remaining)
