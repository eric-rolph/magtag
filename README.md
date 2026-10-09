# MagTag Weather Station: Offline PM2.5 and BME280 Monitor

A standalone **CircuitPython weather station for the Adafruit MagTag**. Monitor
PM2.5 air quality, temperature, humidity, and pressure on its 296 × 128 e-paper
display. Runs without a computer, Wi-Fi, or cloud account when powered separately.

## Features

- **Home dashboard:** large temperature and PM2.5 readings, humidity, pressure,
  estimated AQI, and sensor reading ages.
- **Seven-day history:** one-minute readings, six graph ranges from 1 hour to
  1 week, visible data gaps, and peak-preserving plots.
- **Weather measurements:** Fahrenheit/Celsius, dew point, five-minute PM2.5
  average, three-hour pressure change, and a Zambretti pressure outlook.
- **Estimated NowCast AQI:** uses completed hours and coverage checks; works with
  elapsed session hours when calendar time is unavailable.
- **Persistent storage:** alternating CRC32-checked snapshots, recovery from an
  incomplete save, saved display preferences, and separate previous-session graphs.
- **Controls and alerts:** button hints, hold-D home shortcut, setting feedback,
  and a high-contrast alert banner visible with LEDs off.
- **Sensor recovery:** independent retries, diagnostics, a watchdog, and
  background save steps that keep servicing controls.

[View the twelve-screen UI preview](display-preview.png).
It uses synthetic readings and a desktop font approximation.

## Hardware and installation

- Adafruit MagTag with a battery or another independent power source.
- BME280 temperature, humidity, and pressure sensor on I²C.
- PM2.5 sensor supported by `adafruit_pm25.i2c`.
- **Tested firmware: CircuitPython 7.1.1.** Compiled libraries must match the
  firmware generation.

1. Install CircuitPython on the MagTag. For an existing working 7.1.1 setup,
   keep its compatible `lib/` directory.
2. Install `adafruit_magtag`, `adafruit_bme280`, `adafruit_pm25`,
   `adafruit_display_text`, and their dependencies from the matching
   [Adafruit library bundle](https://github.com/adafruit/Adafruit_CircuitPython_Bundle).
   Firmware-compatible compiled libraries are not vendored here.
3. Copy `code.py`, `boot.py`, and every `weather_*.py` file to `CIRCUITPY`.
   On an existing installation, hold **D during reset** first to enter editing mode.
4. Set your elevation and calibration in [weather_config.py](weather_config.py).
   Reset with D released to start normal device logging.

In normal mode, CircuitPython owns filesystem writes; the computer sees a
read-only drive. Hold D during reset to edit over USB. LEDs default off.

## Buttons

| Button | Action |
| --- | --- |
| A | Open graphs / next metric |
| B | Open graphs / next time range |
| C | LED brightness; hold 1.2 seconds for °F/°C |
| D | Next page; hold 1.2 seconds for Home |

## Validation and development

**374 desktop tests and 63 on-board calculation checks passed.** A 21-day software
replay checks outages, buffer wrap, alerts, and restart recovery. An on-board test
saved and restored all 10,080 samples; the longest save step measured 0.732 seconds.
See [validation notes](docs/validation.md) for scope and limits.

```sh
python -m pip install -r requirements-dev.txt
python tools/validate.py
python tools/replay_weather.py --days 21 --output replay-results.json
```

Use Python 3.10+ for desktop tools. Device source retains Python 3.7 syntax.

[Operation and deployment](docs/operation.md) · [Changelog](CHANGELOG.md) ·
[MIT license](LICENSE)
