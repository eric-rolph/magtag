# MagTag PM-BME-Zambretti Weather Station

This repository now captures the latest known CircuitPython implementation of the Adafruit MagTag weather station.

Current app entry point:
- `code.py`

Features:
- PM2.5 monitoring via PM25 I2C sensor
- Temperature, humidity, and pressure via BME280
- Sea-level pressure calculation
- Zambretti pressure-trend forecast
- Historical graphing on the MagTag e-ink display
- LED air-quality status feedback

Hardware target:
- Adafruit MagTag
- PM2.5 I2C sensor
- BME280 I2C sensor

Python dependencies on device:
- `adafruit_pm25`
- `adafruit_bme280`
- `adafruit_display_text`
- `adafruit_magtag`

## Restore to a MagTag

1. Install a compatible CircuitPython release on the MagTag.
2. Copy `code.py` to the root of the `CIRCUITPY` drive.
3. Install the four dependencies above, including their transitive dependencies,
   from the matching Adafruit CircuitPython library bundle.

The current application is sensor-only and does not need network credentials.
If networking is added later, keep real credentials in `secrets.py` or
`settings.toml`; both are intentionally excluded from Git.

Source provenance:
- Captured from the newest known tracked implementation previously stored at `C:\Users\ericr\source\repos\dev\code.py`
- Audit on 2026-04-29 found no newer or alternate tracked version in this repo
- An older 2025 device snapshot exists locally but is not copied here because it
  contains credentials and predates this implementation.
