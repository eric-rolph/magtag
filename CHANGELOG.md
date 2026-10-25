# Changelog

## 4.0.3 — 2026-10-24

- Guard application imports and runtime failures: preserve a bounded last-crash
  report and restart in normal logging mode. Editing errors and Ctrl-C retain REPL access.
- Arm the watchdog before hardware initialization and service it during history
  restoration, so a startup stall is also covered.
- Recover if the display remains busy or repeatedly rejects refreshes for 60 seconds,
  instead of feeding the watchdog forever with a frozen screen.
- Treat sensor arithmetic/calibration errors as recoverable per-sensor failures.
- Flush each uploaded file before hash verification, then verify CRC32 through
  the MCU before resetting. A flush/readback failure leaves the device paused.
- Add fault tests for startup, runtime, logging failures, stuck displays, and sensor errors.

## 4.0.2 — 2026-10-19

- Fix a crash when tapping D: replace `str.capitalize()`, unavailable on
  CircuitPython 7.1.1, with explicit page labels.
- Add a regression test cycling all pages without that string method.

## 4.0.1 — 2026-10-19

- Start directly from local MagTag peripherals and the built-in display. Avoid
  the legacy network wrapper, which crashes without `secrets.py` on a clean board.
- Add a startup regression check that rejects Wi-Fi and credential imports.

## 4.0 — 2026-10-09

- Add the home dashboard, larger graphs, page hints, and hold-D Home shortcut.
- Explain sensor failures and history warmup; show last-good reading ages.
- Add high-contrast alert banners and setting-change acknowledgments.
- Coalesce navigation while e-paper refreshes are pending.
- Save and verify checkpoints in steps between input and sampling polls.

## 3.0 — 2026-10-09

- Add standalone filesystem ownership, settings validation, independent sensor
  retries, diagnostics, watchdog support, and persistent history/preferences.
- Add dew point, °F/°C, five-minute PM mean, pressure change, estimated NowCast AQI,
  sustained alerts, and previous-session archives.
- Add restart, corruption, and 21-day software replay checks.

## 2.0 — 2026-10-09

- Correct Zambretti formula/table pairs and three-hour pressure trends.
- Record missed intervals as gaps; retain healthy sensor fields during failures.
- Use bounded numeric history and peak-preserving plots.
- Debounce controls, defer e-paper refresh, and power off LEDs at zero brightness.
