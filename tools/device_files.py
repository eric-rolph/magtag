"""Verified deployment/restore with a new local backup before every write.

py -3 tools/device_files.py deploy --drive I:/ --backup-dir ../device-backups
py -3 tools/device_files.py restore --drive I:/ --backup-dir ../device-backups \
    --source ../python-project-backup-YYYYMMDD-HHMMSS/project

Use --port COM3 for automatic, exclusive computer edit mode and hardware resets.
Without a serial port, hold D during reset before updating an installed v3
station, then press Reset without D after the verified copy. Restoration keeps
unrelated files and removes only this application's positively identified boot
launcher when restoring an older backup without one.
"""

import argparse
import ast
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import time


APP_FILES = (
    "weather_config.py",
    "weather_settings.py",
    "weather_core.py",
    "weather_metrics.py",
    "weather_alerts.py",
    "weather_persistence.py",
    "weather_runtime.py",
    "weather_ui.py",
    "weather_boot.py",
    "weather_hardware.py",
    "weather_display.py",
    "weather_station.py",
    "boot.py",
    "code.py",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    return sorted(
        (
            item
            for item in root.rglob("*")
            if item.is_file() and "System Volume Information" not in item.parts
        ),
        key=lambda item: str(item.relative_to(root)),
    )


def backup_device(drive, backup_dir):
    destination = backup_dir / ("device-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    destination.mkdir(parents=True, exist_ok=False)
    manifest = []
    for source in inventory(drive):
        relative = source.relative_to(drive)
        copied = destination / "project" / relative
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copied)
        checksum = digest(source)
        if digest(copied) != checksum:
            raise RuntimeError("Backup mismatch: " + str(relative))
        manifest.append({"path": str(relative), "bytes": source.stat().st_size, "sha256": checksum})
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Verified backup:", destination)
    return destination


def drain(port, seconds):
    deadline = time.monotonic() + seconds
    output = bytearray()
    while time.monotonic() < deadline:
        output.extend(port.read(port.in_waiting or 1))
    return output.decode("utf-8", "replace")


def safe_destination(drive, relative):
    """Resolve before writing/deleting; reject traversal and escaping symlinks."""
    root = drive.resolve()
    destination = (root / relative).resolve()
    if destination == root or root not in destination.parents:
        raise ValueError("Destination escapes the device: " + str(relative))
    return destination


def owned_boot(path):
    """Recognize only our minimal storage launcher, preserving custom boot code."""
    if not path.is_file():
        return False
    try:
        statements = ast.parse(path.read_text(encoding="utf-8")).body
    except (OSError, UnicodeError, SyntaxError):
        return False
    if statements and isinstance(statements[0], ast.Expr) and isinstance(
        statements[0].value, ast.Constant
    ) and isinstance(statements[0].value.value, str):
        statements = statements[1:]
    if len(statements) != 2:
        return False
    imported, called = statements
    return (
        isinstance(imported, ast.ImportFrom)
        and imported.module == "weather_boot"
        and imported.level == 0
        and len(imported.names) == 1
        and imported.names[0].name == "configure_storage"
        and imported.names[0].asname is None
        and isinstance(called, ast.Expr)
        and isinstance(called.value, ast.Call)
        and isinstance(called.value.func, ast.Name)
        and called.value.func.id == "configure_storage"
        and not called.value.args
        and not called.value.keywords
    )


def execute_repl(port, source, marker, seconds=0.8):
    """Verify a whole output line; echoed REPL source cannot count as success."""
    port.write(("exec(" + repr(source) + ")\r\n").encode("utf-8"))
    reply = drain(port, seconds)
    markers = marker if isinstance(marker, tuple) else (marker,)
    lines = [line.strip() for line in reply.splitlines()]
    if not any(expected in lines for expected in markers):
        raise RuntimeError("Device did not confirm " + str(marker) + ": " + reply.strip())
    return reply


def pause_device(port, timeout_seconds=10):
    """Negotiate REPL with bounded retries before allowing any source writes.

    A new USB serial connection can miss its first interrupt. Repeated spaced
    interrupts cancel a running app or incomplete REPL line. Keep each command
    below 256 bytes so older USB/REPL buffers do not drop a long input burst.
    """
    deadline = time.monotonic() + timeout_seconds
    last_error = "no REPL response"

    def wait_output(seconds):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Timed out while pausing the device: " + last_error)
        return drain(port, min(seconds, remaining))

    def command(source, marker):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Timed out while pausing the device: " + last_error)
        return execute_repl(port, source, marker, seconds=min(2, remaining))

    wait_output(0.2)
    for _ in range(3):
        try:
            port.write(b"\x03")
            wait_output(0.4)
            port.write(b"\x03")
            wait_output(0.4)
            port.write(b"\r\n")
            wait_output(0.3)
            command(
                "import supervisor\nsupervisor.disable_autoreload()\n"
                "print('WEATHER_AUTORELOAD_OFF')\n",
                "WEATHER_AUTORELOAD_OFF",
            )
            watchdog_check = (
                "import microcontroller\n_weather_watchdog = microcontroller.watchdog\n"
                "print('WEATHER_UPDATE_PAUSED' if _weather_watchdog is None or "
                "_weather_watchdog.mode is None else 'WEATHER_WATCHDOG_ACTIVE')\n"
            )
            reply = command(
                watchdog_check, ("WEATHER_UPDATE_PAUSED", "WEATHER_WATCHDOG_ACTIVE")
            )
            if "WEATHER_UPDATE_PAUSED" in [line.strip() for line in reply.splitlines()]:
                return
            command(
                "_weather_watchdog.deinit() if hasattr(_weather_watchdog, 'deinit') "
                "else setattr(_weather_watchdog, 'mode', None)\n"
                "print('WEATHER_WATCHDOG_STOPPED')\n",
                "WEATHER_WATCHDOG_STOPPED",
            )
            command(watchdog_check, "WEATHER_UPDATE_PAUSED")
            return
        except RuntimeError as error:
            last_error = str(error)
            if time.monotonic() >= deadline:
                break
    raise RuntimeError("Could not pause sampling and watchdog: " + last_error)


def hard_reset(port):
    """Reset boot.py as well as code.py; a soft reload cannot change FAT ownership."""
    port.write(b"import microcontroller; microcontroller.reset()\r\n")
    try:
        port.flush()
    except OSError:
        # USB disconnecting after the reset command is expected.
        pass
    port.close()
    time.sleep(0.5)


def reconnect(port_name, drive, serial_factory, timeout_seconds=15):
    """Wait for both USB storage and serial, then stop the new application."""
    deadline = time.monotonic() + timeout_seconds
    last_error = "device not connected"
    while time.monotonic() < deadline:
        port = None
        try:
            if (drive / "boot_out.txt").is_file():
                port = serial_factory(port_name, 115200, timeout=0.1)
                pause_device(port, timeout_seconds=max(0.1, deadline - time.monotonic()))
                return port
        except (OSError, RuntimeError) as error:
            last_error = str(error)
        if port is not None:
            port.close()
        time.sleep(0.25)
    raise RuntimeError("USB did not reconnect within 15 seconds: " + last_error)


def editing_mode(port):
    reply = execute_repl(
        port,
        "import weather_boot\n"
        "print('WEATHER_MODE=' + str(int(weather_boot.editing_mode())))\n"
        "print('WEATHER_MODE_CONFIRMED')\n",
        "WEATHER_MODE_CONFIRMED",
    )
    lines = [line.strip() for line in reply.splitlines()]
    if "WEATHER_MODE=1" in lines:
        return True
    if "WEATHER_MODE=0" in lines:
        return False
    raise RuntimeError("Device storage mode was not reported")


def verify_magtag(drive):
    boot_output = drive / "boot_out.txt"
    if not boot_output.is_file():
        raise SystemExit("Destination must be a mounted CIRCUITPY drive with boot_out.txt")
    text = boot_output.read_text(encoding="utf-8")
    if "Adafruit MagTag" not in text:
        raise SystemExit("Destination is not the expected MagTag")
    return text


def copy_verified(files, source_root, drive, remove_owned_boot=False):
    """Copy dependencies first and the entry point last, checking every hash."""
    files = sorted(
        files,
        key=lambda item: (
            2 if item.relative_to(source_root) == Path("code.py")
            else 1 if item.relative_to(source_root) == Path("boot.py") else 0
        ),
    )
    if remove_owned_boot:
        boot = safe_destination(drive, "boot.py")
        if owned_boot(boot):
            boot.unlink()
            print("Removed the v3 boot launcher for the original backup")
    for source in files:
        relative = source.relative_to(source_root)
        destination = safe_destination(drive, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if digest(source) != digest(destination):
            raise RuntimeError("Device copy mismatch: " + str(relative))
    print("Verified", len(files), "files on", drive)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("deploy", "restore"))
    parser.add_argument("--drive", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--port")
    args = parser.parse_args()
    drive = args.drive.resolve()
    backup_dir = args.backup_dir.resolve()
    boot_output = verify_magtag(drive)
    if backup_dir == drive or drive in backup_dir.parents:
        raise SystemExit("The backup must be on a different drive/directory")
    project = Path(__file__).resolve().parents[1]
    source_root = (args.source or project).resolve()
    if args.action == "restore" and args.source is None:
        raise SystemExit("Restore requires --source pointing at a backup's project folder")
    if source_root == drive or drive in source_root.parents:
        raise SystemExit("Source must be a local project or backup, outside the device")
    files = (
        [source_root / name for name in APP_FILES]
        if args.action == "deploy"
        else inventory(source_root)
    )
    if (
        not files
        or any(not item.is_file() for item in files)
        or not (source_root / "code.py").is_file()
    ):
        raise SystemExit("Source is incomplete; device not modified")
    installed_storage = (drive / "weather_boot.py").is_file() and owned_boot(drive / "boot.py")
    if (
        installed_storage and not args.port
        and "Weather storage: computer editing" not in boot_output
    ):
        raise SystemExit(
            "Standalone logging owns the filesystem. Use --port COM3, or hold D while "
            "resetting for computer editing. No device source files were changed."
        )
    port = None
    copied = False
    backup = None
    try:
        if args.port:
            import serial

            port = serial.Serial(args.port, 115200, timeout=0.1)
            pause_device(port)
            if installed_storage:
                # Ask the installed boot helper rather than trying to disable
                # FAT's concurrent-write protection while USB is connected.
                if not editing_mode(port):
                    execute_repl(
                        port,
                        "weather_boot.request_edit_mode(True)\nprint('WEATHER_EDIT_REQUESTED')\n",
                        "WEATHER_EDIT_REQUESTED",
                    )
                    hard_reset(port)
                    port = None
                    port = reconnect(args.port, drive, serial.Serial)
                    verify_magtag(drive)
                    if not editing_mode(port):
                        raise RuntimeError("Hardware reset did not enter computer editing mode")
        # Sampling and the watchdog are stopped before the verified snapshot.
        # No application source file is changed until this backup completes.
        backup = backup_device(drive, backup_dir)
        copy_verified(
            files, source_root, drive,
            remove_owned_boot=args.action == "restore" and not (source_root / "boot.py").is_file(),
        )
        copied = True
        if port is not None:
            hard_reset(port)
            port = None
            # Reconnect without interrupting the normal logging application.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                candidate = None
                try:
                    if (drive / "boot_out.txt").is_file():
                        candidate = serial.Serial(args.port, 115200, timeout=0.1)
                        reply = drain(candidate, 8)
                        port = candidate
                        print(reply)
                        break
                except OSError:
                    if candidate is not None:
                        candidate.close()
                time.sleep(0.25)
            if port is None:
                raise RuntimeError(
                    "Files verified; reset sent, but USB reconnection was not confirmed"
                )
        else:
            print("Press Reset without holding D to start normal operation.")
    except (OSError, RuntimeError, ValueError) as error:
        state = "Files copied" if copied else "Update stopped before a complete copy"
        recovery = " Verified backup: " + str(backup) if backup is not None else ""
        raise SystemExit(
            state + ": " + str(error) + ". Device remains paused if connected; "
            "use computer editing to retry or restore." + recovery
        ) from error
    finally:
        if port:
            port.close()


if __name__ == "__main__":
    main()
