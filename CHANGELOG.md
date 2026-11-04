# Changelog

## 4.2.2 — 2026-11-03

- Lower the first lit LED setting from 12% to 2%, with an additional 5% step;
  preserve saved brightness indices and the hardware power-off setting.
- Explicitly show `Charge unknown` on Measurements with USB/external power.
  Keep the upper-right source indicator; no unmeasured charging countdown.
- Document the missing charge-status/current telemetry and the distinction
  between NeoPixel brightness and the fixed board status LEDs.

## 4.2.1 — 2026-11-03

- Remove manual battery confirmation. Infer battery operation from USB state,
  a twenty-second settling period and voltage plausibility/trends.
- Automatically suppress estimates on USB, high/rising voltage, invalid reads
  and data gaps; resume after settling/discharge evidence as appropriate.
- Restore original B graph control and remove all Hold B battery prompts.
- Source detection is an inference: weak charger inputs can resemble a battery.
- Desktop validation: 444 tests, Ruff and Python 3.7 syntax checks passed.
- Installed on the MagTag: three modules verified, 34 layout cases plus
  automatic recovery and original B simulations passed; normal sampling resumed.

## 4.2.0 — 2026-11-03

- Add approximate 5%-step battery percentage after manual battery-only confirmation
  using Hold B; short B advances graph range on release.
- Preserve voltage on Measurements and show runtime as Learning until sufficient
  consistent discharge history exists. Bound learning memory and avoid extra writes.
- Clear estimates on USB, elevated/rising voltage, bad measurements and timing gaps.
- Configure the user's nominal 2200 mAh ICR 18650 and document generic-curve limits.
- Desktop validation: 445 tests, Ruff and Python 3.7 syntax checks passed.
- Installed and verified on CircuitPython 7.1.1: 18 files, 34 layout cases,
  simulated learning/hold-B/USB tests, and normal sensor sampling passed.
- This is not a calibrated fuel gauge and does not fix the earlier filesystem loss.

## 4.1.0 — 2026-10-25

- Implement the Instrument home screen with large League Spartan numeric type,
  two bounded twelve-hour plots, and a humidity/dew-point/pressure/AQI strip.
- Retain sensor ages, battery voltage, all navigation, alerts, and setting feedback.
- Fit long values by measured glyph width; show missing readings explicitly and
  use the built-in font if optional typography assets cannot load.
- Reuse existing sample and refresh scheduling; preserve plot peaks and empty buckets.
- Include font assets and their license in verified USB deployments.
- Validate all pages, extreme/missing values, AQI states, startup plots, idle
  redraw behavior, and font fallback; preview the actual numeric font pixels.

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
