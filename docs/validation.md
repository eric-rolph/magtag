# Validation

Baseline hardware measurements: October 9, 2026, Adafruit MagTag with
CircuitPython 7.1.1. Desktop validation was repeated for 4.0.3 on October 24.

## Crash recovery audit — October 24

The connected drive again contained only the factory Hello World program and
an empty library folder. Its NVM retained the station's marker, and both sensor
addresses responded. No previous application traceback remained. The cause of
the missing files could not be established from this state; filesystem damage
or an earlier reset/reformat must not be presented as a confirmed diagnosis.

Version 4.0.3 addresses gaps found during the audit: application imports and
unexpected runtime errors now preserve a bounded report and restart; hardware
initialization is covered by the watchdog; stuck display states request recovery;
sensor arithmetic faults are isolated; and uploads flush files and verify MCU
readback before resetting. Host SHA-256 verification alone can read cached data.

Desktop validation passes 398 tests, Ruff, and Python 3.7 source syntax checks.
The 21-day accelerated replay passes all invariants. The board passes 29 simulated
control actions and the display timeout/traceback capture checks. An intentionally
raised exception was saved to `/last-crash.txt` and followed by automatic restart.
A deliberately stalled startup also triggered the watchdog and restarted
successfully. The final normal run produced two scheduled readings, saved a
CRC-valid checkpoint, and retained all 14 modules across resets. The intentional
test crash report was removed. Raw transcripts and the pre-repair drive backup
remain local.

The original disappearance of the application remains unexplained. This audit
does not establish long-term hardware endurance, power-loss tolerance, or flash
health. CircuitPython documents Windows safe-eject precautions in its
[filesystem troubleshooting guide](https://docs.circuitpython.org/en/latest/docs/troubleshooting.html).

## Clean installation recovery — October 19

The connected board contained only `print("Hello World!")` and an empty library
folder. After backing it up, the 14 application files and 73 compatible library
files were restored and hash-verified. Startup then reproduced a legacy network
wrapper crash when `secrets.py` was absent. Version 4.0.1 bypasses that wrapper
and initializes the local peripherals and built-in display directly.

The repaired board booted with no credential files present, both sensors ready,
and a 30-second watchdog armed. Startup and a subsequent scheduled reading were
observed after a hardware reset. The new regression test rejects network and
credential imports during station construction.

## Baseline checks

The 4.0.2 D-button fix was also checked on CircuitPython 7.1.1 using 29 simulated
input actions through the production handler: all pages, four metrics, six graph
ranges, eight brightness settings, both temperature units, and hold-D Home.
Preferences and the archived buffer were restored. These were on-board simulated
inputs, not hand-operated button presses or per-page panel refresh checks.

| Check | Result |
| --- | --- |
| Desktop tests | 376 passed, including offline startup and D-button compatibility |
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
