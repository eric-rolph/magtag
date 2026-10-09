# SPDX-License-Identifier: MIT
"""Weather calculations, time-aware history and controls; no hardware imports."""

from array import array
import math
import sys


def valid_number(value, lower, upper):
    """Reject unavailable, nonnumeric, nonfinite and out-of-range measurements."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and lower <= value <= upper
    )


def sea_level_pressure(pressure_hpa, temperature_c, elevation_m):
    """Approximate sea-level pressure using the original temperature correction."""
    if not (
        valid_number(pressure_hpa, 300, 1100)
        and valid_number(temperature_c, -40, 85)
        and valid_number(elevation_m, -500, 9000)
    ):
        return None
    kelvin = temperature_c + 0.0065 * elevation_m + 273.15
    base = 1.0 - 0.0065 * elevation_m / kelvin
    return pressure_hpa / pow(base, 5.257) if base > 0 else None


def pm_category(concentration):
    """EPA concentration bands applied to the CURRENT reading, not official AQI."""
    if not valid_number(concentration, 0, 3276.7):
        return "Unknown", (100, 100, 100)
    # EPA truncates PM2.5 concentration to one decimal before using breakpoints.
    epsilon = 1e-6 if sys.implementation.name == "circuitpython" else 1e-12
    tenth = int(concentration * 10 + max(1e-7, concentration * 10 * epsilon))
    bands = (
        (90, "Good", (0, 128, 0)),
        (354, "Moderate", (225, 225, 0)),
        (554, "Sensitive", (255, 126, 0)),
        (1254, "Unhealthy", (200, 0, 0)),
        (2254, "V.Unhealthy", (128, 0, 128)),
    )
    for upper, name, color in bands:
        if tenth <= upper:
            return name, color
    return "Hazardous", (100, 0, 0)


class DataHistory:
    """Fixed-memory minute slots, including gaps, stored to 0.1-unit precision.

    Four signed-16-bit arrays need 80,640 payload bytes for one week. Queries
    scan the selected window without copying it. Monotonic time anchors slots.
    """

    MISSING = -32768

    def __init__(self, max_samples=10080, interval_seconds=60, start_time=None):
        if not isinstance(max_samples, int) or max_samples < 1:
            raise ValueError("History capacity must be a positive integer")
        if not valid_number(interval_seconds, 1, 3600):
            raise ValueError("Sampling interval must be between 1 and 3600 seconds")
        self.max_samples = max_samples
        self.interval_seconds = interval_seconds
        self._series = tuple(array("h", [self.MISSING]) * max_samples for _ in range(4))
        self.write_index = 0
        self.data_count = 0
        self._origin = start_time
        self._last_slot = -1
        self._last_timestamp = None

    @property
    def storage_bytes(self):
        return 4 * self.max_samples * 2

    def _encode(self, value):
        if not valid_number(value, -3276.7, 3276.7):
            return self.MISSING
        return int(value * 10 + (0.5 if value >= 0 else -0.5))

    def _append(self, values):
        for series, value in zip(self._series, values):
            series[self.write_index] = self._encode(value)
        self.write_index = (self.write_index + 1) % self.max_samples
        self.data_count = min(self.data_count + 1, self.max_samples)

    def add_reading(self, pm, pressure, temperature_f, humidity, timestamp):
        if not valid_number(timestamp, 0, 1e12):
            raise ValueError("Invalid monotonic timestamp")
        if self._origin is None:
            self._origin = timestamp
        slot = int((timestamp - self._origin) / self.interval_seconds + 1e-7)
        if (
            timestamp < self._origin
            or slot < self._last_slot
            or (self._last_timestamp is not None and timestamp < self._last_timestamp)
        ):
            raise ValueError("History time must not go backwards")
        self._last_timestamp = timestamp
        values = (pm, pressure, temperature_f, humidity)
        if slot == self._last_slot:
            index = (self.write_index - 1) % self.max_samples
            for series, value in zip(self._series, values):
                series[index] = self._encode(value)
            return
        # Bound work even after a delay longer than the entire retention period.
        missing_slots = min(slot - self._last_slot - 1, self.max_samples - 1)
        for _ in range(missing_slots):
            self._append((None, None, None, None))
        self._append(values)
        self._last_slot = slot

    def value_ago(self, series_index, samples_ago=0):
        if not 0 <= series_index < 4:
            raise IndexError("Unknown sensor series")
        if samples_ago < 0 or samples_ago >= self.data_count:
            return None
        encoded = self._series[series_index][
            (self.write_index - 1 - samples_ago) % self.max_samples
        ]
        return None if encoded == self.MISSING else encoded / 10.0

    def summarize(self, series_index, window_samples, graph_width):
        """Return min/mean/max, coverage and per-pixel extrema preserving spikes.

        Empty time slots remain empty; startup data aligns at the right edge of
        the requested time window. Allocation is bounded by screen width.
        """
        if window_samples < 1 or graph_width < 1:
            raise ValueError("Window and graph width must be positive")
        count = min(self.data_count, window_samples)
        columns = [None] * graph_width
        minimum = maximum = None
        total = 0
        valid = 0
        for offset in range(count):
            value = self.value_ago(series_index, count - 1 - offset)
            if value is None:
                continue
            minimum = value if minimum is None else min(minimum, value)
            maximum = value if maximum is None else max(maximum, value)
            total += self._series[series_index][
                (self.write_index - count + offset) % self.max_samples
            ]
            valid += 1
            slot = window_samples - count + offset
            x = slot * (graph_width - 1) // max(1, window_samples - 1)
            if columns[x] is None:
                columns[x] = [value, value]
            else:
                columns[x][0] = min(columns[x][0], value)
                columns[x][1] = max(columns[x][1], value)
        return minimum, total / (valid * 10) if valid else None, maximum, valid, count, columns

    def pressure_trend(self, window_seconds=10800, threshold_hpa=1.6, max_gap_seconds=300):
        """Compare actual three-hour endpoints; require 80% usable coverage."""
        intervals = int(math.ceil(window_seconds / self.interval_seconds))
        if self.data_count <= intervals:
            return "-"
        newest = self.value_ago(1)
        oldest = self.value_ago(1, intervals)
        if newest is None or oldest is None:
            return "-"
        valid = 0
        gap = 0
        for ago in range(intervals + 1):
            if self.value_ago(1, ago) is None:
                gap += 1
                if gap * self.interval_seconds > max_gap_seconds:
                    return "-"
            else:
                valid += 1
                gap = 0
        if valid / (intervals + 1) < 0.8:
            return "-"
        newest_index = (self.write_index - 1) % self.max_samples
        oldest_index = (newest_index - intervals) % self.max_samples
        delta = self._series[1][newest_index] - self._series[1][oldest_index]
        threshold = threshold_hpa * 10
        tolerance = max(1e-7, abs(threshold) * 1e-6)
        if delta >= threshold - tolerance:
            return "rising"
        if delta <= -threshold + tolerance:
            return "falling"
        return "steady"


class ZambrettiForecaster:
    """Pressure-only approximation; no measured wind or seasonal correction.

    Formula/table pairing and pressure domains are documented by SAS:
    github.com/sascommunities/iot-zambretti-weather-forcasting
    Wording is shortened for the display. This is a heuristic outlook.
    """

    TABLES = {
        "falling": (
            985,
            1050,
            127,
            0.12,
            1,
            (
                "Settled fine",
                "Fine weather",
                "Fine, less settled",
                "Fair, showers later",
                "Showery, worsening",
                "Unsettled, rain later",
                "Rain, worsening",
                "Rain, very unsettled",
                "Very unsettled, rain",
            ),
        ),
        "steady": (
            960,
            1033,
            144,
            0.13,
            10,
            (
                "Settled fine",
                "Fine weather",
                "Fine, possible showers",
                "Fair, showers likely",
                "Showers, bright intervals",
                "Changeable, some rain",
                "Unsettled, rain at times",
                "Frequent rain",
                "Very unsettled, rain",
                "Stormy, much rain",
            ),
        ),
        "rising": (
            947,
            1030,
            185,
            0.16,
            20,
            (
                "Settled fine",
                "Fine weather",
                "Becoming fine",
                "Fair, improving",
                "Fair, early showers",
                "Early showers, improving",
                "Changeable, improving",
                "Unsettled, clearing",
                "Unsettled, improving",
                "Unsettled, fine intervals",
                "Very unsettled, breaks",
                "Stormy, may improve",
                "Stormy, much rain",
            ),
        ),
    }

    def get_forecast(self, pressure_hpa, trend):
        if trend not in self.TABLES or pressure_hpa is None:
            return "Outlook: waiting for 3h"
        lower, upper, intercept, slope, start, messages = self.TABLES[trend]
        if not valid_number(pressure_hpa, lower, upper):
            return "Outlook: outside range"
        z = int(intercept - slope * pressure_hpa + 0.5)
        index = max(0, min(z - start, len(messages) - 1))
        return "Outlook: " + messages[index]


class ButtonTracker:
    """Stable-edge debounce: one event per press, including held buttons."""

    def __init__(self, count=4, debounce_seconds=0.05):
        self._raw = [False] * count
        self._stable = [False] * count
        self._changed_at = [0.0] * count
        self.debounce_seconds = debounce_seconds

    def update(self, pressed, now):
        events = []
        for index, value in enumerate(pressed):
            if value != self._raw[index]:
                self._raw[index] = value
                self._changed_at[index] = now
            if (
                value != self._stable[index]
                and now - self._changed_at[index] >= self.debounce_seconds
            ):
                self._stable[index] = value
                if value:
                    events.append(index)
        return events


class SamplingSchedule:
    """Keep original phase while skipping overdue reads, never catch-up bursts."""

    def __init__(self, now, interval_seconds=60):
        if not valid_number(interval_seconds, 1, 3600):
            raise ValueError("Invalid sampling interval")
        self.next_due = now
        self.interval_seconds = interval_seconds

    def due(self, now):
        return now >= self.next_due

    def advance(self, now):
        skipped = int(max(0, now - self.next_due) // self.interval_seconds)
        self.next_due += (skipped + 1) * self.interval_seconds
