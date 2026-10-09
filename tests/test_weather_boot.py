import sys
from types import SimpleNamespace

import pytest
import weather_boot as boot


def install_boundary(monkeypatch, pressed=False):
    nvm = bytearray(8192)
    sleep = bytearray(4096)
    calls = []

    class Pin:
        value = not pressed

        def __init__(self, *_):
            pass

        def deinit(self):
            calls.append("deinit")

    monkeypatch.setitem(sys.modules, "microcontroller", SimpleNamespace(nvm=nvm))
    monkeypatch.setitem(sys.modules, "alarm", SimpleNamespace(sleep_memory=sleep))
    monkeypatch.setitem(sys.modules, "board", SimpleNamespace(BUTTON_D="D"))
    monkeypatch.setitem(
        sys.modules,
        "digitalio",
        SimpleNamespace(
            DigitalInOut=Pin, Direction=SimpleNamespace(INPUT=1), Pull=SimpleNamespace(UP=1)
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "storage",
        SimpleNamespace(remount=lambda path, **kwargs: calls.append((path, kwargs))),
    )
    return nvm, sleep, calls


def test_default_storage_has_one_device_writer(monkeypatch):
    _, _, calls = install_boundary(monkeypatch)
    boot.configure_storage()
    assert not boot.editing_mode()
    assert calls == ["deinit", ("/", {"readonly": False})]


@pytest.mark.parametrize("physical", [False, True])
def test_edit_request_is_consumed_once_and_desktop_is_only_writer(monkeypatch, physical):
    nvm, sleep, calls = install_boundary(monkeypatch, pressed=physical)
    if not physical:
        boot.request_edit_mode()
    boot.configure_storage()
    assert boot.editing_mode()
    assert calls[-1] == ("/", {"readonly": True})
    assert nvm[-8] == 0
    if not physical:
        sleep[:] = bytes(len(sleep))
        boot.configure_storage()
        assert not boot.editing_mode()
        assert calls[-1] == ("/", {"readonly": False})


def test_edit_request_does_not_overwrite_other_nvm(monkeypatch):
    nvm, _, _ = install_boundary(monkeypatch)
    nvm[-1] = 42
    before = bytes(nvm)
    with pytest.raises(RuntimeError, match="overlaps"):
        boot.request_edit_mode()
    assert bytes(nvm) == before
