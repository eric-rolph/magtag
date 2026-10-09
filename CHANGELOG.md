# Changelog

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
