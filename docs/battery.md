# Automatic battery estimates (Instrument v4.2.1)

No button presses or mode changes are needed. The configured battery is a nominal
2200 mAh, 3.7 V ICR 18650. B retains its original graph-range function.

## What appears on the display

- With MagTag USB detected by the computer: `USB`, with voltage on Measurements.
- After USB removal: `AUTO` while checking power for about 20 seconds.
- With plausible battery-range voltage: approximate percentage rounded to 5%,
  such as `~75%`. Measurements shows voltage and time left.
- Runtime shows `Learning` until enough consistent discharge history exists.
- Output above 4.22 V: `EXT`. Significant rising voltage: `PWR ?` and
  `Voltage rising`. Invalid ADC: `BAT --`. Below 3.0 V: `LOW`.

Reconnecting to a USB host suppresses estimates within the ten-second poll.
A rise over 60 mV from the session minimum also suppresses them until voltage
falls at least 20 mV from its subsequent peak, then settles for 20 seconds.
This can conservatively suppress estimates after a normal voltage rebound.
Restart, missing/invalid USB state, invalid ADC readings, timing gaps over two
minutes and backwards time discard stale learning. LED brightness changes
restart runtime learning without requiring user interaction.

## Inferred power source, not measured battery charge

The BQ24074 charger LOAD OUT connects to the MagTag battery socket, so the ADC
measures LOAD OUT, not the cell terminal. External power can regulate it to 4.4 V.
PGOOD and CHG are not wired to GPIO. The charger supplies no digital percentage
or remaining-runtime value.

Without these extra signals, software cannot distinguish every charger input
condition from battery power. A weak external supply or charging voltage within
the cell's range may be treated as battery operation. `~` explicitly marks this
as an approximate voltage-based estimate; it is not proof that the battery is
discharging. Automatic USB/high/rising-voltage checks reduce misleading estimates
but cannot eliminate this ambiguity. A fuel gauge connected to the actual cell
and/or additional source sensing would improve reliability.

CircuitPython 7's USB flag reports enumeration, not a physical 5 V measurement.
A power-only USB adapter or suspended/disconnected USB host can therefore be
missed as an external source. Those cases still use the voltage checks, with
the same limitations described above.

Percentage uses a generic configurable curve, not a calibration for this cell.
Load, temperature, age and charger voltage drop affect the result. Nominal 2200
mAh capacity is recorded in settings, not used as a measured current or capacity.

## Runtime learning

After a five-minute settling period, runtime requires at least two hours and ten
percentage points of decline. Both halves of the observation window must show
discharge, with rates within a factor of two. The average percentage decline is
extrapolated; only estimates up to 48 hours are displayed, rounded to hours or
`<1h`. Flat or inconsistent readings keep showing `Learning`.

At most 49 points are kept in RAM, one every five minutes (up to four hours).
Learning resets after restarting; it adds no flash writes or changes to weather
snapshots. Hidden voltage changes do not request extra Home panel refreshes.
The generic nonlinear curve can still make runtime inaccurate. No shutdown or
battery-protection behavior depends on these estimates.

## Validation

Desktop tests cover automatic activation, USB return, external/high/rising
voltage, automatic recovery, invalid ADC, missing USB capability, timing gaps,
bounded learning, flat-voltage refusal, runtime learning, brightness changes,
and screen bounds/overlap. Hardware results are recorded in validation.json.
No physical discharge calibration of the user's battery has been performed.

References:
- [BQ24074 pinouts](https://learn.adafruit.com/adafruit-bq24074-universal-usb-dc-solar-charger-breakout/pinouts)
- [MAX17048 fuel gauge](https://learn.adafruit.com/adafruit-max17048-lipoly-liion-fuel-gauge-and-battery-monitor/overview)
