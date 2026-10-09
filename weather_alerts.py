# SPDX-License-Identifier: MIT
"""Cadence-aware indicative alerts with sustained activation and hysteresis."""

import math


def _valid(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


class SustainedAlert:
    """Evaluate only observed, continuous conditions; unknown time never counts.

    An active condition is remembered during missing data, but ``unknown`` becomes
    true and ``state`` reports unknown. Callers must check unknown before displaying
    an active alert. The next fresh sample resumes evaluation without credit for the gap.
    """

    def __init__(self, threshold, clear_threshold, duration_seconds,
                 clear_duration_seconds=120, maximum_gap_seconds=120):
        if (
            not all(_valid(value) for value in (
                threshold, clear_threshold, duration_seconds,
                clear_duration_seconds, maximum_gap_seconds,
            )) or threshold <= 0 or clear_threshold < 0 or clear_threshold >= threshold
            or duration_seconds <= 0 or clear_duration_seconds <= 0 or maximum_gap_seconds <= 0
        ):
            raise ValueError("Alert thresholds and durations must be finite with valid hysteresis.")
        self.threshold = threshold
        self.clear_threshold = clear_threshold
        self.duration_seconds = duration_seconds
        self.clear_duration_seconds = clear_duration_seconds
        self.maximum_gap_seconds = maximum_gap_seconds
        self.active = False
        self.unknown = True
        self._pending_since = None
        self._clearing_since = None
        self._last_valid_at = None

    @property
    def state(self):
        if self.unknown:
            return "unknown"
        if self._clearing_since is not None:
            return "clearing"
        if self.active:
            return "active"
        if self._pending_since is not None:
            return "pending"
        return "clear"

    def update(self, value, now):
        """Return the remembered activation flag, with freshness exposed separately."""
        if not _valid(value) or value < 0 or not _valid(now):
            self.unknown = True
            self._pending_since = None
            self._clearing_since = None
            self._last_valid_at = None
            return self.active
        if self._last_valid_at is not None:
            elapsed = now - self._last_valid_at
            if elapsed < 0 or elapsed > self.maximum_gap_seconds:
                self._pending_since = None
                self._clearing_since = None
        self._last_valid_at = now
        self.unknown = False
        if self.active:
            self._pending_since = None
            if value <= self.clear_threshold:
                if self._clearing_since is None:
                    self._clearing_since = now
                elif now - self._clearing_since >= self.clear_duration_seconds:
                    self.active = False
                    self._clearing_since = None
            else:
                self._clearing_since = None
        else:
            self._clearing_since = None
            if value >= self.threshold:
                if self._pending_since is None:
                    self._pending_since = now
                elif now - self._pending_since >= self.duration_seconds:
                    self.active = True
                    self._pending_since = None
            else:
                self._pending_since = None
        return self.active


class AlertManager:
    """Maintain PM average and absolute pressure-change alert conditions."""

    def __init__(self, config):
        self.enabled = config.ALERT_ENABLED
        maximum_gap = max(120, config.READING_INTERVAL_SECONDS * 2)
        self.pm = SustainedAlert(
            config.PM_ALERT_THRESHOLD, config.PM_ALERT_CLEAR_THRESHOLD,
            config.PM_ALERT_DURATION_SECONDS, config.ALERT_CLEAR_DURATION_SECONDS, maximum_gap,
        )
        self.pressure = SustainedAlert(
            config.PRESSURE_ALERT_THRESHOLD_HPA, config.PRESSURE_ALERT_CLEAR_THRESHOLD_HPA,
            config.PRESSURE_ALERT_DURATION_SECONDS,
            config.ALERT_CLEAR_DURATION_SECONDS, maximum_gap,
        )
        self.active_names = ()

    def update(self, pm_average, pressure_delta, now):
        if not self.enabled:
            self.active_names = ()
            return self.active_names
        self.pm.update(pm_average, now)
        pressure_value = abs(pressure_delta) if _valid(pressure_delta) else None
        self.pressure.update(pressure_value, now)
        self.active_names = tuple(
            name for name, alert in (("PM high", self.pm), ("Pressure change", self.pressure))
            if alert.active and not alert.unknown
        )
        return self.active_names

    @property
    def text(self):
        if not self.enabled:
            return "Alerts: disabled"
        if self.active_names:
            return "Alert: " + ", ".join(self.active_names)
        if self.pm.unknown or self.pressure.unknown:
            return "Alerts: data pending"
        return "Alerts: clear"
