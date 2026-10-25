# Operation and deployment

## Power and USB

The application needs no desktop process or network connection. Use a charged
battery or suitable independent power supply. See the MagTag
[power connections](https://learn.adafruit.com/adafruit-magtag/pinouts).
Sampling runs continuously; deep sleep is not enabled and battery runtime is
unmeasured. Board tests used USB power; unplugged operation and sensor power
wiring still need a physical check.

Version 4.0.1 initializes the local peripherals and built-in display directly.
It does not construct the legacy MagTag network wrapper or require `secrets.py`,
`settings.toml`, or Wi-Fi credentials.

Hold D during reset to give the computer write ownership. Storage and the
watchdog pause for that editing session. Reset with D released to resume device
logging. Holding D while the app runs returns Home instead.

In normal mode, an unexpected startup or runtime exception records up to 2 KiB
in `/last-crash.txt`, then restarts after five seconds. Identical reports do not
rewrite flash. Full or read-only storage does not prevent restarting. Ctrl-C
and editing-mode errors retain REPL access. A display stuck busy, or repeatedly
failing refreshes, requests recovery after 60 seconds; normal panel cooldown
does not count as a fault. Recovery cannot repair missing application files,
failed flash storage, or a hardware fault that persists after reset.

## Settings

Edit `weather_config.py` in editing mode. Check `ELEVATION_M`: the supplied
1609 m value came from the original installation. Temperature and pressure
calibration offsets default to zero. Units, sampling interval, history capacity,
graph ranges, retry timers, brightness, persistence, and alert thresholds are
configurable. Invalid settings produce startup errors.

## History and estimates

The Instrument home screen uses the supplied League Spartan bitmap fonts for
numbers. Missing font files or an unavailable font library fall back to the
built-in font. Long readings reduce their integer font scale instead of losing
digits or colliding with units.

The small temperature and PM2.5 plots always cover the latest twelve hours of
the current session. Each plot retains per-column minima and maxima; missing
buckets stay empty and startup data aligns at the right. Their vertical ranges
scale independently; open Graphs with A to see numeric axes or change the range.
They are redrawn on a sample or navigation change, never on idle ticks. The
existing sample cadence and e-paper cooldown are unchanged.

Header `B:` and `P:` show the ages of the last successful BME280 and PM readings
at the most recent refresh. `--` means no successful reading yet. Battery is
voltage, not a charge percentage. `DEW F`/`DEW C` identifies dew-point units.
AQI bands are `GOOD`, `MOD`, `USG` (sensitive groups), `UNH`, `VUNH`, and `HAZ`.
`WAIT` means insufficient history; `OFF` means current PM is unavailable. The
Measurements page retains full category names and reasons. Home shows `999+`
above 999; Measurements retains the full estimate.

The default ring holds 10,080 one-minute slots in an 80,640-byte numeric payload.
Missed samples remain gaps. Graphs preserve each column's minimum and maximum;
stored measurements have 0.1-unit precision.

History saves every 15 minutes, with the first save after 30 seconds. Preferences
save after a 30-second delay. CRC32-checked alternating files retain the previous
valid snapshot. Power loss can discard unsaved changes. Background saves freeze
an extra 80,640-byte ring and write or verify at most 512 payload bytes per step.
E-paper changes wait for the panel's refresh cooldown.

CircuitPython 7.1.1 resets the RTC on hard reset. Without trusted UTC, old history
is shown separately as an archive and current measurements start a new session.
Archive readings never feed current estimates. Optional trusted UTC allows
time-aligned history recovery.

NowCast requires two usable hours among the latest three completed hours, each
with at least 75% coverage. Pressure trends require three-hour endpoints,
at least 80% coverage, and no gap longer than five minutes. Warmup messages
explain why an estimate is unavailable.

AQI is a local sensor estimate based on
[EPA NowCast guidance](https://document.airnow.gov/technical-assistance-document-for-the-reporting-of-daily-air-quailty.pdf).
The pressure-only Zambretti outlook uses
[paired formulas and tables](https://github.com/sascommunities/iot-zambretti-weather-forcasting).
Neither sensor calibration nor forecast accuracy has been measured.

## Default alerts

| Condition | Activate | Clear |
| --- | --- | --- |
| Five-minute PM2.5 mean ≥35.5 µg/m³ | Sustained 5 minutes | ≤30 µg/m³ for 2 minutes |
| Absolute three-hour pressure change ≥3 hPa | Sustained 5 minutes | ≤2 hPa for 2 minutes |

Missing readings interrupt pending timers. A black-and-white banner displays
active conditions even with the LEDs off.

## Deployment helper

Install desktop dependencies first. Adjust the drive and serial port as needed:

```sh
python tools/device_files.py deploy --drive I:/ --backup-dir ../device-backups --port COM3
```

The helper makes a verified backup, enters exclusive editing mode, copies all
14 application files and three font/license assets with `code.py` last, flushes each file,
verifies SHA-256
on the host and CRC32 through the MCU, and restarts logging. Failed flushes or
MCU readback stop the upload before the final reset. Without `--port`, use the
operating system's safe-eject action before manually resetting or unplugging.
Install matching libraries separately for a new board.

To restore a backup:

```sh
python tools/device_files.py restore --drive I:/ --backup-dir ../device-backups --source ../device-backups/YOUR-BACKUP/project --port COM3
```

Backups can contain device credentials. Keep them outside the public repository.
