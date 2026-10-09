import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools import device_files


def fake_device(tmp_path):
    device = tmp_path / "CIRCUITPY"
    device.mkdir()
    (device / "boot_out.txt").write_text("Adafruit CircuitPython 7.1.1; Adafruit MagTag")
    (device / "code.py").write_text("original code")
    (device / "secrets.py").write_text("private configuration")
    return device


def test_deploy_verifies_backup_and_preserves_device_credentials(tmp_path, monkeypatch):
    device = fake_device(tmp_path)
    source = tmp_path / "project"
    source.mkdir()
    for name in device_files.APP_FILES:
        (source / name).write_text("new " + name)
    backups = tmp_path / "backups"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "device_files",
            "deploy",
            "--drive",
            str(device),
            "--source",
            str(source),
            "--backup-dir",
            str(backups),
        ],
    )
    device_files.main()
    backup = next(backups.iterdir())
    assert (backup / "project" / "code.py").read_text() == "original code"
    assert (backup / "manifest.json").is_file()
    assert (device / "code.py").read_text() == "new code.py"
    assert (device / "secrets.py").read_text() == "private configuration"


def test_restore_copies_backup_and_keeps_unrelated_files(tmp_path, monkeypatch):
    device = fake_device(tmp_path)
    (device / "unrelated.txt").write_text("keep")
    original = tmp_path / "original"
    original.mkdir()
    (original / "code.py").write_text("restored code")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "device_files",
            "restore",
            "--drive",
            str(device),
            "--source",
            str(original),
            "--backup-dir",
            str(tmp_path / "backups"),
        ],
    )
    device_files.main()
    assert (device / "code.py").read_text() == "restored code"
    assert (device / "unrelated.txt").read_text() == "keep"


def test_backup_cannot_be_written_inside_device(tmp_path, monkeypatch):
    device = fake_device(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "device_files",
            "deploy",
            "--drive",
            str(device),
            "--backup-dir",
            str(device / "backups"),
        ],
    )
    with pytest.raises(SystemExit, match="different"):
        device_files.main()
    assert (device / "code.py").read_text() == "original code"


def configure_arguments(monkeypatch, device, source, backups, action="deploy", port=None):
    arguments = [
        "device_files", action, "--drive", str(device), "--source", str(source),
        "--backup-dir", str(backups),
    ]
    if port is not None:
        arguments += ["--port", port]
    monkeypatch.setattr(sys, "argv", arguments)


def new_project(tmp_path):
    source = tmp_path / "project"
    source.mkdir()
    for name in device_files.APP_FILES:
        (source / name).write_text("new " + name)
    return source


def standalone_device(tmp_path, editing=False):
    device = fake_device(tmp_path)
    (device / "weather_boot.py").write_text("installed storage helper")
    (device / "boot.py").write_text(
        '# SPDX-License-Identifier: MIT\n"""Storage launcher."""\n'
        "from weather_boot import configure_storage\nconfigure_storage()\n"
    )
    if editing:
        with (device / "boot_out.txt").open("a") as output:
            output.write("\nWeather storage: computer editing\n")
    return device


def test_app_file_list_covers_all_current_device_modules():
    root = Path(device_files.__file__).resolve().parents[1]
    assert set(device_files.APP_FILES) == {path.name for path in root.glob("*.py")}
    assert device_files.APP_FILES[-1] == "code.py"
    assert device_files.APP_FILES[-2] == "boot.py"


@pytest.mark.parametrize("relative", ["../elsewhere.py", "..", "."])
def test_device_destination_cannot_escape_resolved_root(tmp_path, relative):
    drive = tmp_path / "drive"
    drive.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        device_files.safe_destination(drive, relative)
    assert device_files.safe_destination(drive, "nested/state.bin") == drive / "nested/state.bin"


@pytest.mark.parametrize("source,owned", [
    ("from weather_boot import configure_storage\nconfigure_storage()\n", True),
    (
        '"""Our launcher."""\nfrom weather_boot import configure_storage\nconfigure_storage()\n',
        True,
    ),
    ("# from weather_boot import configure_storage\nprint('custom boot')\n", False),
    (
        "from weather_boot import configure_storage\nconfigure_storage()\nprint('custom boot')",
        False,
    ),
    ("from weather_boot import configure_storage as custom\ncustom()\n", False),
    ("from weather_boot import configure_storage\nconfigure_storage(True)\n", False),
    ("malformed Python is not our boot", False),
])
def test_boot_ownership_requires_only_the_known_launcher(tmp_path, source, owned):
    path = tmp_path / "boot.py"
    path.write_text(source)
    assert device_files.owned_boot(path) is owned


def test_installed_standalone_requires_serial_or_confirmed_physical_edit_mode(
    tmp_path, monkeypatch
):
    device = standalone_device(tmp_path)
    source = new_project(tmp_path)
    backups = tmp_path / "backups"
    configure_arguments(monkeypatch, device, source, backups)
    with pytest.raises(SystemExit, match="hold D"):
        device_files.main()
    assert not backups.exists()
    assert (device / "code.py").read_text() == "original code"


def test_restore_removes_only_owned_boot_after_verified_backup(tmp_path, monkeypatch):
    device = standalone_device(tmp_path, editing=True)
    source = tmp_path / "original"
    source.mkdir()
    (source / "code.py").write_text("restored original code")
    backups = tmp_path / "backups"
    configure_arguments(monkeypatch, device, source, backups, action="restore")
    device_files.main()
    backup = next(backups.iterdir())
    assert device_files.owned_boot(backup / "project" / "boot.py")
    assert not (device / "boot.py").exists()
    assert (device / "weather_boot.py").is_file()  # No broad cleanup of extra modules.
    assert (device / "code.py").read_text() == "restored original code"


def test_restore_preserves_custom_boot_even_when_it_imports_our_helper(tmp_path, monkeypatch):
    device = standalone_device(tmp_path, editing=True)
    custom = (
        "from weather_boot import configure_storage\nconfigure_storage()\n"
        "print('user boot behavior')\n"
    )
    (device / "boot.py").write_text(custom)
    source = tmp_path / "original"
    source.mkdir()
    (source / "code.py").write_text("restored code")
    configure_arguments(monkeypatch, device, source, tmp_path / "backups", action="restore")
    device_files.main()
    assert (device / "boot.py").read_text() == custom


def test_entry_point_is_copied_after_all_dependencies_and_boot(tmp_path, monkeypatch):
    device = fake_device(tmp_path)
    source = new_project(tmp_path)
    copied = []
    real_copy = device_files.shutil.copy2

    def tracked_copy(original, destination):
        copied.append(original.name)
        return real_copy(original, destination)

    monkeypatch.setattr(device_files.shutil, "copy2", tracked_copy)
    device_files.copy_verified(list(source.iterdir())[::-1], source, device)
    assert copied[-2:] == ["boot.py", "code.py"]
    assert set(copied[:-2]) == set(device_files.APP_FILES) - {"boot.py", "code.py"}


def test_serial_standalone_update_pauses_before_backup_and_uses_hardware_resets(
    tmp_path, monkeypatch
):
    device = standalone_device(tmp_path)
    source = new_project(tmp_path)
    backups = tmp_path / "backups"
    events = []
    editing = False

    class Port:
        def close(self):
            pass

    def pause(port):
        events.append("pause")

    def mode(port):
        events.append("mode")
        return editing

    def execute(port, command, marker):
        assert "request_edit_mode(True)" in command
        events.append("request edit")

    def reset(port):
        nonlocal editing
        events.append("hard reset")
        editing = not editing

    def reconnect(name, drive, serial_factory):
        assert editing
        events.append("reconnect paused")
        return Port()

    real_backup = device_files.backup_device
    real_copy = device_files.copy_verified

    def backup(drive, backup_dir):
        assert editing
        events.append("backup")
        return real_backup(drive, backup_dir)

    def copy(*args, **kwargs):
        assert events[-1] == "backup"
        events.append("copy")
        return real_copy(*args, **kwargs)

    monkeypatch.setitem(
        sys.modules, "serial", SimpleNamespace(Serial=lambda *args, **kwargs: Port())
    )
    monkeypatch.setattr(device_files, "pause_device", pause)
    monkeypatch.setattr(device_files, "editing_mode", mode)
    monkeypatch.setattr(device_files, "execute_repl", execute)
    monkeypatch.setattr(device_files, "hard_reset", reset)
    monkeypatch.setattr(device_files, "reconnect", reconnect)
    monkeypatch.setattr(device_files, "backup_device", backup)
    monkeypatch.setattr(device_files, "copy_verified", copy)
    monkeypatch.setattr(
        device_files, "drain", lambda port, seconds: "Weather storage: device logging"
    )
    configure_arguments(monkeypatch, device, source, backups, port="COM3")
    device_files.main()
    assert events == [
        "pause", "mode", "request edit", "hard reset", "reconnect paused", "mode",
        "backup", "copy", "hard reset",
    ]
    assert (next(backups.iterdir()) / "project" / "code.py").read_text() == "original code"


def test_serial_pause_failure_does_not_create_backup_or_change_source(tmp_path, monkeypatch):
    device = standalone_device(tmp_path)
    source = new_project(tmp_path)
    backups = tmp_path / "backups"
    monkeypatch.setitem(
        sys.modules, "serial",
        SimpleNamespace(Serial=lambda *args, **kwargs: SimpleNamespace(close=lambda: None)),
    )

    def failed_pause(port):
        raise RuntimeError("watchdog did not stop")

    monkeypatch.setattr(device_files, "pause_device", failed_pause)
    configure_arguments(monkeypatch, device, source, backups, port="COM3")
    with pytest.raises(SystemExit, match="watchdog did not stop"):
        device_files.main()
    assert not backups.exists()
    assert (device / "code.py").read_text() == "original code"


def test_repl_echo_is_not_accepted_as_confirmation(monkeypatch):
    port = SimpleNamespace(write=lambda data: len(data))
    monkeypatch.setattr(device_files, "drain", lambda *args: ">>> print('PAUSED')\n>>> ")
    with pytest.raises(RuntimeError, match="did not confirm"):
        device_files.execute_repl(port, "print('PAUSED')", "PAUSED")


def test_pause_retries_missed_interrupt_and_keeps_repl_commands_under_usb_buffer(monkeypatch):
    clock = [0]
    written = []
    commands = []
    port = SimpleNamespace(write=lambda data: written.append(data))
    monkeypatch.setattr(device_files.time, "monotonic", lambda: clock[0])

    def drain(port, seconds):
        clock[0] += seconds
        return ""

    def execute(port, source, marker, seconds):
        commands.append(marker)
        wire = ("exec(" + repr(source) + ")\r\n").encode("utf-8")
        assert len(wire) < 256
        clock[0] += seconds
        if len(commands) == 1:
            raise RuntimeError("initial USB interrupt was missed")
        if isinstance(marker, tuple):
            return "WEATHER_UPDATE_PAUSED\n"
        return marker + "\n"

    monkeypatch.setattr(device_files, "drain", drain)
    monkeypatch.setattr(device_files, "execute_repl", execute)
    device_files.pause_device(port)
    assert written.count(b"\x03") == 4
    assert written.count(b"\r\n") == 2
    assert commands[0] == commands[1] == "WEATHER_AUTORELOAD_OFF"
    assert clock[0] <= 10


def test_pause_disarms_active_watchdog_and_confirms_result(monkeypatch):
    commands = []
    port = SimpleNamespace(write=lambda data: len(data))
    monkeypatch.setattr(device_files, "drain", lambda *args: "")

    def execute(port, source, marker, seconds):
        commands.append(marker)
        assert len(("exec(" + repr(source) + ")\r\n").encode("utf-8")) < 256
        if isinstance(marker, tuple):
            return "WEATHER_WATCHDOG_ACTIVE\n"
        return marker + "\n"

    monkeypatch.setattr(device_files, "execute_repl", execute)
    device_files.pause_device(port)
    assert commands[-2:] == ["WEATHER_WATCHDOG_STOPPED", "WEATHER_UPDATE_PAUSED"]


def test_pause_gives_up_on_unresponsive_device_within_deadline(monkeypatch):
    clock = [0]
    commands = []
    port = SimpleNamespace(write=lambda data: len(data))
    monkeypatch.setattr(device_files.time, "monotonic", lambda: clock[0])

    def drain(port, seconds):
        clock[0] += seconds
        return ""

    def execute(port, source, marker, seconds):
        commands.append(marker)
        clock[0] += seconds
        raise RuntimeError("no reply")

    monkeypatch.setattr(device_files, "drain", drain)
    monkeypatch.setattr(device_files, "execute_repl", execute)
    with pytest.raises(RuntimeError, match="Could not pause"):
        device_files.pause_device(port, timeout_seconds=5)
    assert len(commands) == 2
    assert clock[0] <= 5
