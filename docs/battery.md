# Battery estimates (Instrument v4.2.0)

The configured battery is a nominal 2200 mAh, 3.7 V ICR 18650. The BQ24074
charger's LOAD OUT connects to the MagTag battery socket. That means the
MagTag ADC measures LOAD OUT, not a direct battery terminal. External power can
regulate this output to 4.4 V. PGOOD and CHG are not connected to GPIO, and the
charger provides no digital state-of-charge or remaining-runtime reading.

## Using the estimate

1. Disconnect USB from the MagTag and all USB/DC/solar inputs from the charger.
   Keep the battery connected to LiPo BATT and MagTag connected to LOAD OUT.
2. Hold **B for 1.2 seconds**, then release, to confirm battery-only operation.
   Home shows an approximate percentage rounded to 5%, such as `~75%`.
3. Tap D to open Measurements for voltage and estimated time left. `Learning`
   means there is not enough consistent discharge history for a runtime estimate.
4. Hold B again to clear the confirmation before reconnecting charger power.
   Direct MagTag USB also clears it automatically within the ten-second poll.

Without confirmation the corner reads `PWR ?`; with MagTag USB it reads `USB`.
An output above 4.22 V reads `EXT`. Invalid ADC readings show `BAT --`.
Voltage remains on Measurements. The clock line moves out of Measurements;
clock status remains on Diagnostics. A short B press still advances graph range,
now on release, and a long B press does not change the graph.

## Accuracy and learning

Percentage uses a generic, configurable voltage curve, **not a calibrated curve
for this specific cell**. Load, temperature, age and voltage drop through the
charger affect it. Nominal capacity is recorded in settings; it is not a
measurement of usable capacity or current consumption. A percentage-derived
runtime calculation does not require assuming a battery current from that label.

Runtime starts with a five-minute settling period, then requires at least two
hours and ten percentage points of decline. Both halves of that window must
show discharge, with rates within a factor of two. It extrapolates the average
percentage decline and only displays estimates up to 48 hours, rounded to hours
(or `<1h`). This is a rough trend estimate, not a guaranteed remaining duration.
The generic nonlinear voltage curve can make the trend inaccurate even after
those checks. No automatic shutdown or battery protection depends on it.

At most 49 observations are kept in RAM (one every five minutes, up to four
hours). Learning resets on restart, LED brightness change, a sample gap over
two minutes, or backwards time. Confirmation also clears on a gap, invalid
voltage, direct USB, output above 4.22 V, or a rise over 60 mV from the session
minimum. Voltage rebound may conservatively clear confirmation.

These checks **cannot reliably detect every external charger input change**.
A weak external supply can resemble battery voltage; direct USB detection does
not detect power plugged into the charger. Always clear confirmation before
connecting charger power. Fully automatic operation needs additional source
sensing and/or a fuel gauge measuring the actual cell, such as MAX17048.

Battery learning adds no filesystem writes, and does not alter weather snapshots.
Updating a voltage hidden on Home does not request an extra panel refresh.

References:
- [BQ24074 pinouts](https://learn.adafruit.com/adafruit-bq24074-universal-usb-dc-solar-charger-breakout/pinouts)
- [MAX17048 fuel gauge](https://learn.adafruit.com/adafruit-max17048-lipoly-liion-fuel-gauge-and-battery-monitor/overview)

## Validation

Desktop tests cover power-state rejection, manual confirmation, USB return,
voltage rebound, invalid ADC, missing USB capability, timing gaps, bounded
learning, flat-voltage refusal, runtime learning, brightness changes, gestures,
and layout bounds/overlaps. Battery estimates have not been calibrated against
a measured discharge of this cell. Hardware deployment is tracked separately;
desktop tests do not establish on-device operation.

November 3 hardware validation: installed 18 application/font/license files
with host SHA-256 and MCU CRC32 verification. On CircuitPython 7.1.1,
34 visible-glyph layout cases, simulated discharge learning, simulated Hold B,
USB detection and normal sensor sampling passed. Physical discharge calibration
and a physical Hold B battery-only test are not part of this validation.
