from types import SimpleNamespace

import pytest
import weather_config as config
from weather_hardware import LEDManager, SensorData


class PM:
    def read(self):
        return {"pm25 env": 12}


def bme(temperature=20, humidity=40, pressure=835):
    return SimpleNamespace(temperature=temperature, humidity=humidity, pressure=pressure)


@pytest.mark.parametrize("failed_field", ["temperature", "humidity", "pressure"])
def test_independent_bme_fields_survive_partial_failures(failed_field):
    device = bme()
    setattr(device, failed_field, None)
    sensors = SensorData(object(), config, lambda *_: PM(), lambda *_, **__: device)
    assert sensors.read_sensors(0)
    assert sensors.pm == 12
    if failed_field != "temperature":
        assert sensors.temperature_f == pytest.approx(68)
    if failed_field != "humidity":
        assert sensors.humidity == 40
    if failed_field in ("temperature", "pressure"):
        assert sensors.pressure is None


def test_disconnected_sensor_does_not_prevent_other_sensor_and_retries():
    attempts = []

    def pm_factory(*_):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("Disconnected")
        return PM()

    sensors = SensorData(object(), config, pm_factory, lambda *_, **__: bme())
    assert sensors.read_sensors(0)
    assert sensors.pm is None
    assert sensors.temperature_f == 68
    sensors.read_sensors(10)
    assert len(attempts) == 1
    sensors.read_sensors(60)
    assert sensors.pm == 12
    assert len(attempts) == 2


def test_bme_address_fallback():
    addresses = []

    def factory(_, address):
        addresses.append(address)
        if address == 0x77:
            raise ValueError("No device")
        return bme()

    sensors = SensorData(object(), config, lambda *_: PM(), factory)
    sensors.read_sensors(0)
    assert addresses == [0x77, 0x76]
    assert sensors.pressure is not None


def test_repeated_read_failures_reinitialize_and_clear_stale_pm():
    class BrokenPM:
        def read(self):
            raise RuntimeError("Checksum")

    devices = [BrokenPM(), PM()]
    sensors = SensorData(object(), config, lambda *_: devices.pop(0), lambda *_, **__: bme())
    for now in (0, 60, 120):
        sensors.read_sensors(now)
        assert sensors.pm is None
    sensors.read_sensors(180)
    assert sensors.pm == 12


def test_led_off_switches_power_off_and_restores_latest_color():
    class Pixels:
        auto_write = True
        brightness = 1
        shows = 0
        color = None

        def fill(self, color):
            self.color = color

        def show(self):
            self.shows += 1

    peripherals = SimpleNamespace(neopixels=Pixels(), neopixel_disable=False)
    leds = LEDManager(peripherals, (0, 0.25), 1)
    leds.set_color((255, 0, 0))
    shows = peripherals.neopixels.shows
    leds.set_color((255, 0, 0))
    assert peripherals.neopixels.shows == shows
    leds.cycle_brightness()
    assert peripherals.neopixel_disable
    leds.set_color((0, 128, 0))
    leds.cycle_brightness()
    assert not peripherals.neopixel_disable
    assert peripherals.neopixels.color == (0, 128, 0)


def test_retry_timer_recovers_before_next_sample_and_tracks_health():
    attempts = []

    def factory(*_):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("unplugged")
        return PM()

    sensors = SensorData(object(), config, factory, lambda *_, **__: bme())
    sensors.read_sensors(0)
    assert sensors.health(0)[0]["errors"] == 1
    assert sensors.health(0)[0]["last_good"] is None
    sensors.service_retries(29)
    assert len(attempts) == 1
    sensors.service_retries(30)
    assert len(attempts) == 2 and sensors._devices[0] is not None
    sensors.read_sensors(60)
    assert sensors.health(61)[0]["state"] == "ready"
    assert sensors.health(61)[0]["last_good"] == 60
