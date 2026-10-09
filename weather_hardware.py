# SPDX-License-Identifier: MIT
"""Recoverable sensor drivers and LED power management."""

from weather_core import pm_category, sea_level_pressure, valid_number


class SensorData:
    """Initialize/recover each sensor separately; keep successful readings."""

    def __init__(self, i2c, config, pm_factory=None, bme_factory=None):
        if pm_factory is None:
            from adafruit_pm25.i2c import PM25_I2C

            pm_factory = PM25_I2C
        if bme_factory is None:
            from adafruit_bme280.basic import Adafruit_BME280_I2C

            bme_factory = Adafruit_BME280_I2C
        self.i2c = i2c
        self.config = config
        self._factories = (pm_factory, bme_factory)
        self._devices = [None, None]
        self._retry_at = [0.0, 0.0]
        self._errors = [0, 0]
        self.total_errors = [0, 0]
        self.last_good_at = [None, None]
        self._status = [None, None]
        self.pm = self.temperature_c = self.temperature_f = None
        self.humidity = self.pressure_raw = self.pressure = None

    def _report(self, index, state, error=None):
        if self._status[index] != state:
            name = ("PM2.5", "BME280")[index]
            print(name, state, str(error) if error is not None else "")
            self._status[index] = state

    def _device(self, index, now):
        if self._devices[index] is not None:
            return self._devices[index]
        if now < self._retry_at[index]:
            return None
        self._retry_at[index] = now + self.config.SENSOR_RETRY_SECONDS
        try:
            if index == 0:
                device = self._factories[0](self.i2c, None)
            else:
                device = None
                last_error = None
                for address in self.config.BME280_ADDRESSES:
                    try:
                        device = self._factories[1](self.i2c, address=address)
                        break
                    except (OSError, RuntimeError, ValueError) as error:
                        last_error = error
                if device is None:
                    raise RuntimeError("BME280 not found: " + str(last_error))
            self._devices[index] = device
            self._errors[index] = 0
            return device
        except (OSError, RuntimeError, ValueError) as error:
            self.total_errors[index] += 1
            self._report(index, "unavailable", error)
            return None

    def _failed(self, index, now, error):
        self._errors[index] += 1
        self.total_errors[index] += 1
        self._report(index, "read failed", error)
        if self._errors[index] >= self.config.SENSOR_RESET_AFTER_ERRORS:
            self._devices[index] = None
            self._retry_at[index] = now + self.config.SENSOR_RETRY_SECONDS

    def _success(self, index, now):
        self._errors[index] = 0
        self.last_good_at[index] = now
        self._report(index, "ready")

    def service_retries(self, now):
        """Reconnect on its own timer rather than waiting for the next sample."""
        for index in range(2):
            if self._devices[index] is None and now >= self._retry_at[index]:
                self._device(index, now)

    def health(self, now):
        return tuple(
            {
                "state": self._status[index] or "initializing",
                "errors": self.total_errors[index],
                "streak": self._errors[index],
                "last_good": self.last_good_at[index],
                "retry_in": max(0, int(self._retry_at[index] - now))
                if self._devices[index] is None
                else 0,
            }
            for index in range(2)
        )

    def read_sensors(self, now):
        self.pm = self.temperature_c = self.temperature_f = None
        self.humidity = self.pressure_raw = self.pressure = None
        pm = self._device(0, now)
        if pm is not None:
            try:
                value = pm.read()["pm25 env"]
                if not valid_number(value, 0, 3276.7):
                    raise ValueError("Invalid PM2.5 concentration")
                self.pm = value
                self._success(0, now)
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
                self._failed(0, now, error)
        bme = self._device(1, now)
        if bme is not None:
            usable = 0
            last_error = None
            for attribute, lower, upper in (
                ("temperature", -40, 85),
                ("humidity", 0, 100),
                ("pressure", 300, 1100),
            ):
                try:
                    value = getattr(bme, attribute)
                    if not valid_number(value, lower, upper):
                        raise ValueError("Invalid " + attribute)
                    if attribute == "temperature":
                        self.temperature_c = value + self.config.TEMPERATURE_OFFSET_C
                        self.temperature_f = self.temperature_c * 1.8 + 32
                    elif attribute == "humidity":
                        self.humidity = value
                    else:
                        self.pressure_raw = value + self.config.PRESSURE_OFFSET_HPA
                    usable += 1
                except (OSError, RuntimeError, ValueError, TypeError) as error:
                    last_error = error
            self.pressure = sea_level_pressure(
                self.pressure_raw, self.temperature_c, self.config.ELEVATION_M
            )
            if usable == 3:
                self._success(1, now)
            else:
                self._failed(1, now, last_error)
        return any(
            value is not None
            for value in (self.pm, self.temperature_f, self.humidity, self.pressure)
        )

    def get_air_quality_category(self):
        return pm_category(self.pm)


class LEDManager:
    def __init__(self, peripherals, levels, brightness_index=2):
        self.peripherals = peripherals
        self.pixels = peripherals.neopixels
        self.levels = levels
        self.brightness_index = brightness_index % len(levels)
        self.color = None
        self.pixels.auto_write = False
        self._apply_brightness()

    def _apply_brightness(self):
        brightness = self.levels[self.brightness_index]
        self.pixels.brightness = brightness
        self.peripherals.neopixel_disable = brightness == 0
        if brightness > 0:
            self.pixels.fill(self.color or (0, 0, 0))
            self.pixels.show()

    def set_color(self, color):
        if self.color != color:
            self.color = color
            if self.levels[self.brightness_index] > 0:
                self.pixels.fill(color)
                self.pixels.show()

    def cycle_brightness(self):
        self.brightness_index = (self.brightness_index + 1) % len(self.levels)
        self._apply_brightness()
