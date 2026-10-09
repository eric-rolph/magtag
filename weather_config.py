# SPDX-License-Identifier: MIT
"""User settings. Uses ordinary Python so CircuitPython 7 can read it."""

ELEVATION_M = 1609.0  # Preserves the original site's setting; verify for your location.
TEMPERATURE_OFFSET_C = 0.0  # Apply a measured calibration, not an arbitrary correction.
TEMPERATURE_UNIT = "F"  # "F" or "C"; calibration offsets are always Celsius.
PRESSURE_OFFSET_HPA = 0.0
BME280_ADDRESSES = (0x77, 0x76)
READING_INTERVAL_SECONDS = 60
HISTORY_SAMPLES = 10080  # Seven days at the default one-minute interval.
TREND_WINDOW_SECONDS = 10800  # Three hours, including both endpoints.
TREND_THRESHOLD_HPA = 1.6
TREND_MAX_GAP_SECONDS = 300
SENSOR_RETRY_SECONDS = 30
SENSOR_RESET_AFTER_ERRORS = 3
BUTTON_DEBOUNCE_SECONDS = 0.05
POLL_INTERVAL_SECONDS = 0.02
DEFAULT_GRAPH_HOURS = 12
LED_BRIGHTNESS_INDEX = 0  # Start with LEDs powered off for standalone battery operation.
LED_BRIGHTNESS_LEVELS = (0.0, 0.12, 0.25, 0.38, 0.50, 0.62, 0.75, 1.0)
RESOLUTION_HOURS = (1, 6, 12, 24, 72, 168)

# Checkpoints require CircuitPython ownership of the filesystem. If the computer owns it,
# the application keeps running and reports that storage is unavailable.
PERSISTENCE_ENABLED = True
CHECKPOINT_INTERVAL_SECONDS = 900
CHECKPOINT_DIRECTORY = "/weather-state"
WATCHDOG_ENABLED = True
WATCHDOG_TIMEOUT_SECONDS = 30
DIAGNOSTIC_REFRESH_SECONDS = 60
PM_AVERAGE_SECONDS = 300
HOURLY_MINIMUM_COVERAGE = 0.75

# Indicative conditions from this sensor, not medical warnings or official AQI alerts.
ALERT_ENABLED = True
PM_ALERT_THRESHOLD = 35.5
PM_ALERT_CLEAR_THRESHOLD = 30.0
PM_ALERT_DURATION_SECONDS = 300
PRESSURE_ALERT_THRESHOLD_HPA = 3.0
PRESSURE_ALERT_CLEAR_THRESHOLD_HPA = 2.0
PRESSURE_ALERT_DURATION_SECONDS = 300
ALERT_CLEAR_DURATION_SECONDS = 120
