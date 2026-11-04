import sys
from types import SimpleNamespace

import pytest
import weather_config as config
from weather_battery import BatteryEstimate, usb_power
from weather_settings import validate_config


def battery():
    return BatteryEstimate(config.BATTERY_CAPACITY_MAH, config.BATTERY_VOLTAGE_CURVE)


@pytest.mark.parametrize("usb,voltage", [(True, 4.1), (None, 4), (False, 4.4),
                                       (False, float("nan")), (False, None), (False, 2.9)])
def test_auto_refuses_external_unknown_or_invalid_power(usb, voltage):
    b = battery()
    for now in (0,10,20,30):
        b.update(now,voltage,usb)
    assert b.percent is None and b.hours is None


def test_automatic_estimate_after_settling_and_usb_return():
    b = battery()
    b.update(0, 3.85, False)
    assert b.percent is None and b.text()[0] == "AUTO"
    b.update(10,3.85,False)
    assert b.percent is None
    b.update(20,3.85,False)
    assert b.text()[0] == "~60%"
    assert "Learning" in b.text()[1]
    b.update(30, 3.85, True)
    assert b.text()[0] == "USB" and not b._active
    b.update(40, 3.85, False)
    assert b.text()[0] == "AUTO" and not b._active
    b.update(60,3.85,False)
    assert b.percent is not None


@pytest.mark.parametrize("now,voltage,usb", [(30,4.4,False), (30,3.9,False),
                                          (30,3.8,True), (30,None,False),
                                          (141,3.8,False), (-1,3.8,False)])
def test_estimates_cleared_on_rise_power_change_invalid_read_or_gap(now, voltage, usb):
    b = battery()
    b.update(0,3.8,False)
    b.update(20,3.8,False)
    b.update(now,voltage,usb)
    assert not b._active and b.percent is None and b.hours is None


def discharge(b, end, drop_per_hour=.05, load=0):
    for now in range(0,end+1,60):
        b.update(now, 4.1-drop_per_hour*now/3600,False,load)


def test_runtime_requires_two_hours_and_consistent_meaningful_drop():
    b = battery()
    discharge(b,7200)
    assert b.hours is None  # Five minute settling period precedes learning.
    b.update(-1,4.1,False)
    assert b.hours is None and b.percent is None
    b = battery()
    discharge(b,10800)
    assert b.hours is not None and 5 < b.hours < 15
    b.update(10860,3.95,False,1)
    assert b.hours is None and len(b._points) == 0


def test_flat_voltage_never_generates_runtime_and_memory_is_bounded():
    b = battery()
    for now in range(0,86401,60):
        b.update(now,4,False,0)
    assert b.hours is None and len(b._points) <= 49


def test_rising_voltage_waits_for_a_drop_before_automatic_recovery():
    b = battery()
    b.update(0,3.8,False)
    b.update(20,3.8,False)
    b.update(30,3.9,False)
    for now in range(40,1000,10):
        b.update(now,3.9,False)
        assert b.percent is None
    b.update(1000,3.87,False)
    assert b.text()[0] == "AUTO"
    b.update(1020,3.87,False)
    assert b.percent is not None and b.hours is None


def test_usb_detection_fails_closed(monkeypatch):
    monkeypatch.setitem(sys.modules,"supervisor",SimpleNamespace(runtime=object()))
    assert usb_power() is None
    monkeypatch.setitem(sys.modules,"supervisor",SimpleNamespace(
        runtime=SimpleNamespace(usb_connected=False)))
    assert usb_power() is False


@pytest.mark.parametrize("curve", [None, (), ((4,0),(3,100)), ((3,0),(4,0)),
                                    ((3,0),(4,float('nan'))), ((3,1),(4,100))])
def test_bad_curve_rejected_at_startup(curve):
    settings = SimpleNamespace(**{n:getattr(config,n) for n in dir(config) if n.isupper()})
    settings.BATTERY_VOLTAGE_CURVE = curve
    assert any("BATTERY_VOLTAGE_CURVE" in e for e in validate_config(settings))
