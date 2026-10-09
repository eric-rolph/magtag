# Operation and deployment

## Power and USB

The application needs no desktop process or network connection. Use a charged
battery or suitable independent power supply. See the MagTag
[power connections](https://learn.adafruit.com/adafruit-magtag/pinouts).
Sampling runs continuously; deep sleep is not enabled and battery runtime is
unmeasured. Board tests used USB power; unplugged operation and sensor power
wiring still need a physical check.

Hold D during reset to give the computer write ownership. Storage and the
watchdog pause for that editing session. Reset with D released to resume device
logging. Holding D while the app runs returns Home instead.

## Settings

Edit `weather_config.py` in editing mode. Check `ELEVATION_M`: the supplied
1609 m value came from the original installation. Temperature and pressure
calibration offsets default to zero. Units, sampling interval, history capacity,
graph ranges, retry timers, brightness, persistence, and alert thresholds are
configurable. Invalid settings produce startup errors.

## History and estimates

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
14 application files with `code.py` last, verifies hashes, and restarts logging.
Install matching libraries separately for a new board.

To restore a backup:

```sh
python tools/device_files.py restore --drive I:/ --backup-dir ../device-backups --source ../device-backups/YOUR-BACKUP/project --port COM3
```

Backups can contain device credentials. Keep them outside the public repository.
