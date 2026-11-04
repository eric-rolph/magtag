# SPDX-License-Identifier: MIT
"""Debounced gestures, bounded navigation queue, and readable unavailable states."""

import math
from weather_core import ButtonTracker


class UIController:
    """Keep at most one further navigation intent until the panel catches up.

    C/D short actions occur on debounced release. Holding either for 1.2 seconds
    emits its long action once and suppresses the short action. Home takes
    priority over queued navigation. Sampling and persistence never wait here.
    """

    def __init__(self, debounce_seconds=0.05, hold_seconds=1.2):
        self.buttons = ButtonTracker(debounce_seconds=debounce_seconds)
        self.hold_seconds = hold_seconds
        self._held_at = [None, None]
        self._long_handled = [False, False]
        self.navigation_inflight = False
        self.pending_navigation = None

    def rendered(self):
        self.navigation_inflight = False

    def _navigation(self, action, actions, force=False):
        if force:
            self.pending_navigation = None
        elif self.navigation_inflight:
            self.pending_navigation = action
            return
        self.navigation_inflight = True
        actions.append(action)

    def update(self, pressed, now):
        actions = []
        if self.pending_navigation is not None and not self.navigation_inflight:
            pending = self.pending_navigation
            self.pending_navigation = None
            self._navigation(pending, actions)
        previous = tuple(self.buttons._stable)
        for button in self.buttons.update(pressed, now):
            if button < 2:
                self._navigation(("metric", "range")[button], actions)
            else:
                index = button - 2
                self._held_at[index] = now
                self._long_handled[index] = False
        for index, button in enumerate((2, 3)):
            started = self._held_at[index]
            if started is None:
                continue
            if (
                pressed[button]
                and self.buttons._stable[button]
                and now - started >= self.hold_seconds
                and not self._long_handled[index]
            ):
                self._long_handled[index] = True
                if index == 0:
                    actions.append("units")
                else:
                    self._navigation("home", actions, force=True)
            if previous[button] and not self.buttons._stable[button]:
                if not self._long_handled[index]:
                    if index == 0:
                        actions.append("brightness")
                    else:
                        self._navigation("page", actions)
                self._held_at[index] = None
        return actions


def status_messages(history, sensors, estimate, config, pm_mean, pressure_delta, dew_point):
    """Give a useful reason when an estimate is unavailable, without inventing time."""
    messages = {"aqi_reason": "", "pressure_reason": "", "pm_mean_reason": "", "dew_reason": ""}
    if estimate.get("aqi") is None:
        reason = estimate.get("reason", "")
        messages["aqi_reason"] = (
            "Est AQI: need 2 usable recent hours"
            if reason == "need 2 of last 3 hours"
            else "Est AQI: collecting PM history"
        )
        if sensors.pm is None:
            messages["aqi_reason"] = "Est AQI: PM sensor unavailable"
    else:
        messages["aqi_reason"] = estimate.get("reason", "sensor estimate")
    if pressure_delta is None:
        intervals = int(math.ceil(config.TREND_WINDOW_SECONDS / history.interval_seconds))
        if sensors.pressure is None:
            text = "Pressure sensor unavailable"
        elif history.data_count <= intervals:
            minutes = min(history.data_count - 1, intervals) * history.interval_seconds / 60
            minutes = max(0, int(minutes))
            needed = int(config.TREND_WINDOW_SECONDS / 60)
            text = "Pressure: collecting {} / {} min".format(minutes, needed)
        else:
            text = "Pressure: insufficient recent coverage"
        messages["pressure_reason"] = text
    if pm_mean is None:
        if sensors.pm is None:
            text = "PM sensor unavailable"
        else:
            samples = int(math.ceil(config.PM_AVERAGE_SECONDS / history.interval_seconds))
            required = int(math.ceil(samples * 0.8))
            usable = sum(
                history.value_ago(0, ago) is not None
                for ago in range(min(samples, history.data_count))
            )
            text = "PM mean: collecting {} / {} readings".format(usable, required)
        messages["pm_mean_reason"] = text
    if dew_point is None:
        if sensors.temperature_c is None:
            messages["dew_reason"] = "Dew: temperature unavailable"
        elif sensors.humidity is None:
            messages["dew_reason"] = "Dew: humidity unavailable"
        elif sensors.humidity > 0:
            messages["dew_reason"] = "Dew: outside estimate range"
        else:
            messages["dew_reason"] = "Dew: humidity must exceed zero"
    return messages
