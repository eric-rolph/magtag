# Validation

Tested on October 9, 2026 with an Adafruit MagTag running CircuitPython 7.1.1.

| Check | Result |
| --- | --- |
| Desktop tests | 374 passed |
| Ruff and Python 3.7 device-source syntax | Passed |
| On-board calculation checks | 63 passed, zero failed |
| Installed application files | All 14 matched local SHA-256 hashes |
| Sensors | PM2.5 and BME280 ready |
| Startup and later scheduled reading | Observed after hardware reset |
| Display | Home, graph, measurements, diagnostics, archive; actual font spacing checked |
| Navigation | Simulated 25 presses produced one active and one queued change; hold-D cleared the queue |
| Software replay | 21 simulated days; outages, missing slots, ring wrap, alerts, corruption, restart checks |

## Full-week storage test

A separate temporary directory saved and restored 10,080 synthetic samples
while the production controller processed simulated actions between storage calls.
The temporary directory was removed afterward.

| Measurement | Observed |
| --- | --- |
| Service calls | 324 |
| Longest service call | 0.732 s |
| Longest payload write | 0.380 s |
| Longest verification step | 0.153 s |
| Snapshot creation | 0.075 s |
| Total save and verification | 49.546 s |
| Archive load | 19.235 s |
| Controller actions during save | 41 |
| Watchdog | Armed at 30 s |

The saved snapshot retained the original readings while the live ring changed.
Total save time excludes the normal application's sleep between ticks. Smaller
chunks trade total throughput for input availability; archive loading remains
synchronous at startup.

These are single-run observations, not timing guarantees. USB remained connected.
Physical button feel, unplugged battery operation, runtime, calibration, flash
wear, sensor unplug/replug behavior, and seven-day hardware endurance remain
unmeasured. Accelerated replay is not elapsed physical operation.

The public repository preserves the tested source and a compact
[results record](validation.json). Private device backups and raw serial logs
are kept locally.
