import builtins
import sys
from types import ModuleType, SimpleNamespace

import weather_station
import weather_config as config
from weather_alerts import AlertManager
from weather_core import DataHistory, SamplingSchedule, ZambrettiForecaster
from weather_ui import UIController


def test_startup_without_credentials_or_networking(monkeypatch):
    settings = SimpleNamespace(**{
        name: getattr(config, name) for name in dir(config) if name.isupper()
    })
    settings.PERSISTENCE_ENABLED = False
    settings.WATCHDOG_ENABLED = False
    display = object()
    peripherals = SimpleNamespace(battery=4.0)
    rendered_on = []
    package = ModuleType("adafruit_magtag")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "adafruit_magtag", package)
    monkeypatch.setitem(sys.modules, "adafruit_magtag.peripherals", SimpleNamespace(
        Peripherals=lambda: peripherals,
    ))
    monkeypatch.setitem(sys.modules, "board", SimpleNamespace(
        DISPLAY=display, SCL=object(), SDA=object(),
    ))
    monkeypatch.setitem(sys.modules, "busio", SimpleNamespace(I2C=lambda *a, **k: object()))
    monkeypatch.setitem(sys.modules, "weather_display", SimpleNamespace(
        Display=lambda target, _: rendered_on.append(target) or object(),
    ))
    monkeypatch.setattr(weather_station, "SensorData", lambda *a: object())
    monkeypatch.setattr(weather_station, "LEDManager", lambda *a: object())
    monkeypatch.setattr(weather_station, "editing_mode", lambda: False)
    monkeypatch.setattr(weather_station, "trusted_epoch", lambda: None)
    monkeypatch.setattr(weather_station, "reset_reason", lambda: "POWER_ON")
    original_import = builtins.__import__

    def offline_import(name, *args, **kwargs):
        assert name not in (
            "secrets", "wifi", "adafruit_magtag.magtag", "adafruit_magtag.network",
            "adafruit_portalbase.network",
        ), "Offline startup imported " + name
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", offline_import)
    station = weather_station.WeatherStation(settings)
    assert station.peripherals is peripherals
    assert rendered_on == [display]
    assert not station.watchdog.enabled


def test_watchdog_is_armed_before_hardware_initialization_can_stall(monkeypatch):
    import pytest

    calls = []
    guard = SimpleNamespace(status="armed 30s", feed=lambda: calls.append("feed"))
    monkeypatch.setattr(weather_station, "WatchdogGuard", lambda *args: calls.append("armed")
                        or guard)
    monkeypatch.setattr(weather_station, "editing_mode", lambda: False)
    monkeypatch.setitem(sys.modules, "board", SimpleNamespace(SCL=1, SDA=2))
    package = ModuleType("adafruit_magtag")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "adafruit_magtag", package)
    monkeypatch.setitem(sys.modules, "adafruit_magtag.peripherals", SimpleNamespace(
        Peripherals=lambda: calls.append("peripherals") or object(),
    ))
    monkeypatch.setitem(sys.modules, "weather_display", SimpleNamespace(Display=object))

    def failing_bus(*args, **kwargs):
        calls.append("bus")
        raise OSError("bus failed during startup")

    monkeypatch.setitem(sys.modules, "busio", SimpleNamespace(I2C=failing_bus))
    with pytest.raises(OSError, match="bus failed"):
        weather_station.WeatherStation()
    assert calls[0] == "armed"
    assert calls.index("armed") < calls.index("peripherals") < calls.index("bus")


def test_station_delay_produces_one_real_read_and_visible_missing_slots(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(weather_station.time, "monotonic", lambda: clock[0])
    station = weather_station.WeatherStation.__new__(weather_station.WeatherStation)
    station.config = config
    station.started_at = 0
    station._update_details = lambda *_: None
    station.alerts = AlertManager(config)
    reads = []
    station.sensors = SimpleNamespace(
        pm=12,
        pressure=1013,
        temperature_f=70,
        temperature_c=21,
        humidity=40,
        read_sensors=lambda now: reads.append(now),
        get_air_quality_category=lambda: ("Moderate", (255, 255, 0)),
    )
    station.history = DataHistory(100, start_time=0)
    station.forecaster = ZambrettiForecaster()
    station.schedule = SamplingSchedule(0)
    station.reading_count = 0
    station.peripherals = SimpleNamespace(battery=4.0)
    station.display = SimpleNamespace(update_sensors=lambda *_: None)
    station.leds = SimpleNamespace(set_color=lambda *_: None)
    assert station.update_readings(0)
    clock[0] = 600
    assert station.update_readings(600)
    assert not station.update_readings(600)
    assert reads == [0, 600]
    assert station.history.data_count == 11
    assert station.history.summarize(0, 11, 11)[3] == 2
    assert station.forecast == "Outlook: waiting for 3h"


def test_held_button_does_not_block_or_repeat():
    station = weather_station.WeatherStation.__new__(weather_station.WeatherStation)
    station.controls = UIController()
    calls = []
    station.peripherals = SimpleNamespace(
        button_a_pressed=True,
        button_b_pressed=False,
        button_c_pressed=False,
        button_d_pressed=False,
    )
    station.display = SimpleNamespace(
        next_graph_type=lambda: calls.append("next"),
        graph_index=0,
        DATA_TYPES=("PM2.5", "Pressure", "Temp", "Humidity"),
        acknowledge=lambda *_: None,
    )
    station._update_sensor_labels = lambda: None
    station._update_details = lambda *_: None
    station._changed_preferences = lambda *_: None
    for now in (0, 0.1, 0.2, 60, 600):
        station.check_buttons(now)
    assert calls == ["next"]


def test_short_d_cycles_all_pages_without_cpython_string_helpers():
    class CircuitPythonString(str):
        @property
        def capitalize(self):
            raise AttributeError("CircuitPython 7 does not implement str.capitalize")

    station = weather_station.WeatherStation.__new__(weather_station.WeatherStation)
    station.controls = UIController()
    station.archive = object()
    buttons = SimpleNamespace(
        button_a_pressed=False, button_b_pressed=False,
        button_c_pressed=False, button_d_pressed=False,
    )
    station.peripherals = buttons
    screens = ("home", "weather", "details", "diagnostics", "archive")
    feedback = []

    def next_screen(has_archive):
        assert has_archive
        index = (screens.index(station.display.screen) + 1) % len(screens)
        station.display.screen = CircuitPythonString(screens[index])

    station.display = SimpleNamespace(
        screen=CircuitPythonString("home"), next_screen=next_screen,
        acknowledge=lambda message, _: feedback.append(message),
    )
    station._update_sensor_labels = lambda: None
    station._update_details = lambda *_: None
    for index in range(5):
        now = index * 2
        buttons.button_d_pressed = True
        station.check_buttons(now)
        station.check_buttons(now + 0.1)
        buttons.button_d_pressed = False
        station.check_buttons(now + 0.3)
        station.check_buttons(now + 0.4)
        station.controls.rendered()
    assert station.display.screen == "home"
    assert feedback == ["Graphs", "Measurements", "Diagnostics", "Previous session", "Home"]


def test_c_short_release_changes_brightness_and_long_hold_changes_units_once():
    station = weather_station.WeatherStation.__new__(weather_station.WeatherStation)
    station.controls = UIController()
    calls = []
    peripherals = SimpleNamespace(
        button_a_pressed=False,
        button_b_pressed=False,
        button_c_pressed=False,
        button_d_pressed=False,
    )
    station.peripherals = peripherals
    feedback = []
    station.display = SimpleNamespace(
        toggle_temperature_unit=lambda: calls.append("unit"),
        temperature_unit="C",
        acknowledge=lambda text, _: feedback.append(text),
    )
    station.leds = SimpleNamespace(
        cycle_brightness=lambda: calls.append("brightness"),
        levels=(0, 0.12),
        brightness_index=1,
    )
    station._update_sensor_labels = lambda: None
    station._update_details = lambda *_: None
    station._changed_preferences = lambda *_: None
    peripherals.button_c_pressed = True
    station.check_buttons(0)
    station.check_buttons(0.1)
    peripherals.button_c_pressed = False
    station.check_buttons(0.3)
    station.check_buttons(0.4)
    assert calls == ["brightness"]
    peripherals.button_c_pressed = True
    station.check_buttons(1)
    station.check_buttons(1.1)
    station.check_buttons(2.4)
    station.check_buttons(3)
    peripherals.button_c_pressed = False
    station.check_buttons(3.1)
    station.check_buttons(3.2)
    assert calls == ["brightness", "unit"]
    assert feedback == ["LED brightness: 12%", "Temperature: C"]


def test_watchdog_is_fed_only_after_successful_tick():
    import pytest

    station = weather_station.WeatherStation.__new__(weather_station.WeatherStation)
    calls = []
    station.check_buttons = lambda *_: calls.append("buttons")
    station.sensors = SimpleNamespace(service_retries=lambda *_: calls.append("retry"))
    station.update_readings = lambda *_: calls.append("read")
    station.update_diagnostics = lambda *_: None
    station.history = object()
    station.display = SimpleNamespace(
        screen="weather", update_graph=lambda *_: None, refresh=lambda *_: None
    )
    station.checkpoint = lambda *_: calls.append("checkpoint")
    station.watchdog = SimpleNamespace(feed=lambda: calls.append("feed"))
    station.tick(0)
    assert calls == ["buttons", "retry", "read", "checkpoint", "feed"]
    calls.clear()

    def failure(*_):
        raise RuntimeError("stuck display")

    station.display.refresh = failure
    with pytest.raises(RuntimeError, match="stuck"):
        station.tick(1)
    assert "feed" not in calls


def test_editing_mode_never_writes_checkpoints():
    station = weather_station.WeatherStation.__new__(weather_station.WeatherStation)
    station.config = config
    station.editing = True
    station.checkpoint(100)


def test_tick_services_inputs_and_sampling_before_one_storage_step():
    station = weather_station.WeatherStation.__new__(weather_station.WeatherStation)
    station.config = config
    station.editing = False
    station.controls = SimpleNamespace(rendered=lambda: None)
    calls = []
    station.check_buttons = lambda *_: calls.append("input")
    station.sensors = SimpleNamespace(service_retries=lambda *_: None)
    station.update_readings = lambda *_: calls.append("sample")
    station.update_diagnostics = lambda *_: None
    station.history = object()
    station.display = SimpleNamespace(
        screen="home", update_graph=lambda *_: None, refresh=lambda *_: False
    )
    station.store = SimpleNamespace(saving=True, service_save=lambda: calls.append("storage"))
    station.watchdog = SimpleNamespace(feed=lambda: calls.append("feed"))
    station.tick(1)
    station.tick(2)
    assert calls == ["input", "sample", "storage", "feed"] * 2


def test_checkpoint_starts_one_job_and_preserves_dirty_preferences_until_later():
    station = weather_station.WeatherStation.__new__(weather_station.WeatherStation)
    station.config = config
    station.editing = False
    station._preferences_dirty = True
    station._preferences_due = 100
    station._checkpoint_due = 0
    station.history = object()
    station._preferences = lambda: {"temperature_unit": "C"}
    calls = []
    station.store = SimpleNamespace(
        saving=False,
        start_save=lambda *args, **kwargs: calls.append("start"),
        service_save=lambda: calls.append("step"),
    )
    station.checkpoint(0)
    assert calls == ["start"]
    station.store.saving = True
    station.checkpoint(100)
    assert calls == ["start", "step"]
    assert station._preferences_dirty
