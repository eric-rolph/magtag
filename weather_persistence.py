# SPDX-License-Identifier: MIT
"""Bounded, checksummed A/B checkpoints; never changes USB filesystem ownership.

Files contain an extensible JSON header followed by chronological little-endian
signed-16-bit PM, pressure, Fahrenheit and humidity rows. The trailing CRC32
covers the magic, header length, header and all rows. History restoration needs
a trusted UTC epoch supplied by the caller; an unset RTC restores only settings.
"""

import json
import math
import os
import struct
import time
from array import array

try:
    import binascii

    _native_crc32 = getattr(binascii, "crc32", None)
except ImportError:
    _native_crc32 = None


_MAGIC = b"MWTHST1\n"
_PREFERENCES_MAGIC = b"MWPREF1\n"
_SCHEMA = "pm-p-tf-rh10-v1"
_HEADER_LIMIT = 512
# Match the board's 4 KiB flash erase sector. Smaller writes repeatedly rewrite
# sectors on CircuitPython's FAT filesystem; a fixed 4 KiB buffer remains bounded
# while materially reducing flash churn. This does not change the file format.
_CHUNK_BYTES = 4096
# A cooperative save yields more often than synchronous startup/recovery I/O.
# Each service call packs, writes and checksums at most this much payload, then
# returns to the application's button, sensor and watchdog loop.
_SERVICE_BYTES = 512
_MAX_SOURCE_SAMPLES = 65535
_MIN_EPOCH = 1577836800  # 2020-01-01 UTC; an uninitialized RTC is not trusted.
_MAX_EPOCH = 4102444800  # 2100-01-01 UTC.
_CRC_TABLES = None


def _crc_tables():
    """Keep CRC arithmetic in two small-integer lanes on CircuitPython 7.

    Unsigned 32-bit intermediate values allocate heap integers on this board.
    Two 256-entry unsigned-short tables use 1,024 bytes and remove those heap
    allocations from the byte loop. Build once, only if native CRC is absent.
    """
    global _CRC_TABLES
    if _CRC_TABLES is None:
        table_low = array("H")
        table_high = array("H")
        for value in range(256):
            low, high = value, 0
            for _ in range(8):
                bit = low & 1
                low = (low >> 1) ^ ((high & 1) << 15)
                high >>= 1
                if bit:
                    low ^= 0x8320
                    high ^= 0xEDB8
            table_low.append(low)
            table_high.append(high)
        _CRC_TABLES = (table_low, table_high)
    return _CRC_TABLES


def _crc32(data, previous=0):
    """Incremental ZIP CRC, including firmware builds without binascii.crc32."""
    if _native_crc32 is not None:
        return _native_crc32(data, previous) & 0xFFFFFFFF
    table_low, table_high = _crc_tables()
    low = (previous & 65535) ^ 65535
    high = ((previous >> 16) & 65535) ^ 65535
    for value in data:
        index = (low ^ value) & 255
        low = (low >> 8) ^ ((high & 255) << 8) ^ table_low[index]
        high = (high >> 8) ^ table_high[index]
    return ((high ^ 65535) << 16) | (low ^ 65535)


def _number(value, lower, upper):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and lower <= value <= upper
    )


def _integer(value, lower, upper):
    return isinstance(value, int) and not isinstance(value, bool) and lower <= value <= upper


def _epoch(value):
    return _number(value, _MIN_EPOCH, _MAX_EPOCH)


def _preferences(value):
    if not isinstance(value, dict):
        raise ValueError("invalid preferences")
    clean = {}
    for name in ("graph_index", "resolution_index", "brightness_index"):
        index = value.get(name, 0)
        if not _integer(index, 0, 63):
            raise ValueError("invalid preference index")
        clean[name] = index
    unit = value.get("temperature_unit", "F")
    if unit not in ("C", "F"):
        raise ValueError("invalid temperature unit")
    clean["temperature_unit"] = unit
    return clean


def _read_exact(stream, count):
    data = stream.read(count)
    if len(data) != count:
        raise ValueError("truncated checkpoint")
    return data


def _write_all(stream, data):
    if stream.write(data) != len(data):
        raise OSError("short checkpoint write")


class CheckpointStore:
    """Keep the previous complete slot while overwriting the other slot.

    ``saved_at_epoch`` is UTC checkpoint time. Pass ``now_monotonic`` to save
    when checkpointing between readings, so the newest slot retains its age.
    Epoch arguments must already be trusted by the caller (valid RTC or sync).
    Failure changes ``status`` and returns False/None without crashing sampling.
    """

    def __init__(
        self, directory="/weather-state", max_samples=10080, interval_seconds=60,
        progress_callback=None,
    ):
        if not _integer(max_samples, 1, _MAX_SOURCE_SAMPLES):
            raise ValueError("Invalid checkpoint capacity")
        if not _number(interval_seconds, 1, 3600):
            raise ValueError("Invalid checkpoint interval")
        if progress_callback is not None and not callable(progress_callback):
            raise ValueError("Invalid checkpoint progress callback")
        self.directory = str(directory).rstrip("/\\") or "/"
        self.max_samples = max_samples
        self.interval_seconds = interval_seconds
        self.status = "not loaded"
        self._current_path = None
        self._generation = 0
        self._preferences_current_path = None
        self._preferences_generation = 0
        self._last_log_status = None
        self._last_log_time = None
        self.progress_callback = progress_callback
        self.progress_phase = "idle"
        self.progress_bytes = 0
        self.progress_total = 0
        self._history_checked = False
        self._preferences_checked = False
        self._save_job = None
        self._servicing_save = False

    @property
    def saving(self):
        """True until the inactive checkpoint has been written and verified."""
        return self._save_job is not None

    def _progress(self, phase, completed, total):
        """Yield only after a bounded chunk of real work has completed.

        A callback may feed a watchdog or service UI, but must not reenter this
        store. Cooperative saves own a copy of their source, so live sampling
        may continue while they are pending. It is never called from a timer
        while blocked I/O is pending, so stalled storage still times out.
        """
        self.progress_phase = phase
        self.progress_bytes = completed
        self.progress_total = total
        if self.progress_callback is not None:
            self.progress_callback()

    def _path(self, slot, preferences=False):
        name = "/preferences-" if preferences else "/checkpoint-"
        return self.directory + name + slot + ".bin"

    def _status(self, status, error=False):
        self.status = status
        if not error:
            return
        now = time.monotonic()
        if (
            status != self._last_log_status
            or self._last_log_time is None
            or now - self._last_log_time >= 300
        ):
            print("Checkpoint:", status)
            self._last_log_status = status
            self._last_log_time = now

    def _header(self, stream, preferences=False):
        magic = _read_exact(stream, len(_MAGIC))
        if magic != (_PREFERENCES_MAGIC if preferences else _MAGIC):
            raise ValueError("unknown checkpoint format")
        length_bytes = _read_exact(stream, 2)
        length = struct.unpack("<H", length_bytes)[0]
        if not 1 <= length <= _HEADER_LIMIT:
            raise ValueError("invalid header length")
        raw = _read_exact(stream, length)
        header = json.loads(raw.decode("utf-8"))
        if not isinstance(header, dict):
            raise ValueError("invalid header")
        schema = "preferences-v1" if preferences else _SCHEMA
        if not _integer(header.get("version"), 1, 1) or header.get("schema") != schema:
            raise ValueError("unknown checkpoint schema")
        if not _integer(header.get("generation"), 1, 0x7FFFFFFF):
            raise ValueError("invalid generation")
        header["preferences"] = _preferences(header.get("preferences"))
        prefix = magic + length_bytes + raw
        if preferences:
            return header, prefix
        capacity = header.get("capacity")
        count = header.get("count")
        if not _integer(capacity, 1, _MAX_SOURCE_SAMPLES) or not _integer(count, 0, capacity):
            raise ValueError("invalid history bounds")
        if not _number(header.get("interval"), 1, 3600):
            raise ValueError("invalid sampling interval")
        saved = header.get("saved_at_epoch")
        newest = header.get("latest_slot_epoch")
        fraction = header.get("latest_slot_fraction", 0)
        if saved is not None and not _epoch(saved):
            raise ValueError("invalid saved time")
        if newest is not None and (not _epoch(newest) or saved is None or newest > saved):
            raise ValueError("invalid history time")
        if not _number(fraction, 0, 0.999999999) or (
            fraction and (newest is None or newest == saved)
        ):
            raise ValueError("invalid history time fraction")
        return header, prefix

    def _read_slot(self, path, preferences=False, phase="read"):
        try:
            with open(path, "rb") as stream:
                header, prefix = self._header(stream, preferences)
                crc = _crc32(prefix)
                remaining = 0 if preferences else header["count"] * 8
                total = remaining
                self._progress(phase, 0, total)
                while remaining:
                    chunk = _read_exact(stream, min(remaining, _CHUNK_BYTES))
                    crc = _crc32(chunk, crc)
                    remaining -= len(chunk)
                    self._progress(phase, total - remaining, total)
                expected = struct.unpack("<I", _read_exact(stream, 4))[0]
                if crc != expected or stream.read(1):
                    raise ValueError("checkpoint checksum or length mismatch")
                self._progress(phase, total, total)
            return {"header": header, "prefix": prefix, "path": path}
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RuntimeError):
            return None

    def _select_latest(self):
        first = self._read_slot(self._path("a"))
        second = self._read_slot(self._path("b"))
        if first is None:
            selected = second
        elif second is None:
            selected = first
        else:
            selected = (
                first if first["header"]["generation"] >= second["header"]["generation"]
                else second
            )
        if selected is not None:
            self._current_path = selected["path"]
            self._generation = selected["header"]["generation"]
        self._history_checked = True
        return selected

    def _select_latest_preferences(self):
        selected = None
        for slot in ("a", "b"):
            candidate = self._read_slot(self._path(slot, preferences=True), preferences=True)
            if candidate is not None and (
                selected is None
                or candidate["header"]["generation"] > selected["header"]["generation"]
            ):
                selected = candidate
        if selected is not None:
            self._preferences_current_path = selected["path"]
            self._preferences_generation = selected["header"]["generation"]
        self._preferences_checked = True
        return selected

    def _merged_preferences(self, history_header, preferences_selected):
        prefs_header = preferences_selected["header"] if preferences_selected else None
        if prefs_header is not None and (
            history_header is None or prefs_header["generation"] > history_header["generation"]
        ):
            return dict(prefs_header["preferences"])
        return dict(history_header["preferences"]) if history_header is not None else None

    def _ensure_directory(self):
        try:
            os.stat(self.directory)
        except OSError:
            os.mkdir(self.directory)

    def _sync(self):
        sync = getattr(os, "sync", None)
        if sync is not None:
            sync()
            self._progress("sync", 0, 0)

    def _rows(self, snapshot, count, chunk_bytes):
        chunk = bytearray(_CHUNK_BYTES)
        used = 0
        start = (snapshot["write_index"] - count) % snapshot["capacity"]
        series = snapshot["series"]
        for offset in range(count):
            index = (start + offset) % snapshot["capacity"]
            struct.pack_into(
                "<hhhh", chunk, used,
                series[0][index], series[1][index],
                series[2][index], series[3][index],
            )
            used += 8
            if used == chunk_bytes:
                yield memoryview(chunk)[:used]
                used = 0
        if used:
            yield memoryview(chunk)[:used]

    def _begin_save(
        self, history, preferences, saved_at_epoch, now_monotonic, chunk_bytes
    ):
        if self.saving:
            self._status("save deferred: history save active")
            return False
        try:
            if history.interval_seconds != self.interval_seconds:
                raise ValueError("sampling interval changed")
            if (
                len(history._series) != 4
                or not _integer(history.max_samples, 1, _MAX_SOURCE_SAMPLES)
                or not _integer(history.data_count, 0, history.max_samples)
                or not _integer(history.write_index, 0, history.max_samples - 1)
                or any(len(series) != history.max_samples for series in history._series)
            ):
                raise ValueError("invalid source history")
            clean = _preferences(preferences)
            # CircuitPython builds may use 32-bit floats: doing arithmetic on a
            # floating Unix epoch loses entire minutes. Keep epochs as integers
            # and encode the small fractional-slot phase separately.
            saved = int(saved_at_epoch) if _epoch(saved_at_epoch) else None
            count = min(history.data_count, self.max_samples)
            newest = saved if count else None
            fraction = 0
            if count and saved is not None and now_monotonic is not None:
                slot_time = history._origin + history._last_slot * self.interval_seconds
                if not _number(now_monotonic, slot_time, 1e12):
                    raise ValueError("invalid monotonic checkpoint time")
                age = now_monotonic - slot_time
                age_ceiling = int(math.ceil(age))
                newest = saved - age_ceiling
                fraction = age_ceiling - age
                if not _epoch(newest):
                    newest = None
                    fraction = 0
            # Native array copies freeze all ring values before returning to
            # live sampling. The writer never holds a view of mutable history.
            # Four signed-short arrays cost 8 bytes per source slot (80,640
            # bytes for a full week), with a fixed 4 KiB packing buffer later.
            snapshot = {
                "series": tuple(array("h", series) for series in history._series),
                "capacity": history.max_samples, "write_index": history.write_index,
            }
            if not self._history_checked:
                self._select_latest()
            if not self._preferences_checked:
                self._select_latest_preferences()
            generation = max(self._generation, self._preferences_generation) + 1
            if generation > 0x7FFFFFFF:
                raise ValueError("checkpoint generation exhausted")
            path = self._path("b" if self._current_path == self._path("a") else "a")
            header = {
                "version": 1, "schema": _SCHEMA, "generation": generation,
                "capacity": min(history.max_samples, self.max_samples), "count": count,
                "interval": self.interval_seconds, "saved_at_epoch": saved,
                "latest_slot_epoch": newest, "latest_slot_fraction": fraction,
                "preferences": clean,
            }
            raw = json.dumps(header).encode("utf-8")
            if len(raw) > _HEADER_LIMIT:
                raise ValueError("checkpoint header too large")
            prefix = _MAGIC + struct.pack("<H", len(raw)) + raw
            self._save_job = {
                "path": path, "header": header, "prefix": prefix,
                "rows": self._rows(snapshot, count, chunk_bytes), "stream": None,
                "state": "prefix", "crc": 0, "completed": 0,
                "total": count * 8, "chunk_bytes": chunk_bytes,
            }
            self.progress_phase = "queued"
            self.progress_bytes = 0
            self.progress_total = count * 8
            self._status("saving")
            return True
        except (
            OSError, ValueError, TypeError, AttributeError, OverflowError,
            RuntimeError, MemoryError
        ) as error:
            self._status("save unavailable: " + str(error), error=True)
            return False

    def start_save(self, history, preferences, saved_at_epoch, now_monotonic=None):
        """Snapshot a ring and queue a cooperative save; do not write it yet.

        Call ``service_save`` once per application tick, after handling buttons
        and due sensor readings. Metadata, preferences and all encoded values
        describe this call's instant even if live history changes afterward.
        Startup ``load``/``load_archive`` may still perform synchronous recovery.
        """
        return self._begin_save(
            history, preferences, saved_at_epoch, now_monotonic, _SERVICE_BYTES
        )

    def _close_save_stream(self, job):
        stream = job["stream"]
        job["stream"] = None
        if stream is not None:
            stream.close()

    def cancel_save(self):
        """Close and discard the inactive job, preserving the committed slot.

        Cancellation removes its inactive file when possible, including a file
        whose footer was written but verification has not completed. Failure to
        remove it never damages the prior slot; loading always rechecks CRC.
        """
        if self._servicing_save:
            raise RuntimeError("cannot cancel save from progress callback")
        job = self._save_job
        if job is None:
            return False
        self._save_job = None
        try:
            self._close_save_stream(job)
        except (OSError, RuntimeError):
            pass
        try:
            os.remove(job["path"])
        except OSError:
            pass
        self.progress_phase = "idle"
        self._status("save cancelled")
        return True

    def service_save(self):
        """Do one bounded step: None pending, True committed, False on error.

        An idle call returns None. Each payload step packs, writes/checksums or
        reads/checksums at most 512 bytes. File open/close and filesystem sync
        are separate steps; a stalled filesystem operation still times out via
        the application's watchdog. Progress callbacks run only after work.
        """
        if self._servicing_save:
            raise RuntimeError("checkpoint service cannot reenter itself")
        job = self._save_job
        if job is None:
            return None
        self._servicing_save = True
        try:
            state = job["state"]
            if state == "prefix":
                self._ensure_directory()
                job["stream"] = open(job["path"], "wb")
                _write_all(job["stream"], job["prefix"])
                job["crc"] = _crc32(job["prefix"])
                job["state"] = "payload"
                self._progress("write", 0, job["total"])
            elif state == "payload":
                try:
                    chunk = next(job["rows"])
                except StopIteration:
                    job["state"] = "footer"
                else:
                    _write_all(job["stream"], chunk)
                    job["crc"] = _crc32(chunk, job["crc"])
                    job["completed"] += len(chunk)
                    self._progress("write", job["completed"], job["total"])
            elif state == "footer":
                _write_all(job["stream"], struct.pack("<I", job["crc"]))
                job["state"] = "close"
                self._progress("write", job["total"], job["total"])
            elif state == "close":
                self._close_save_stream(job)
                job["state"] = "sync"
            elif state == "sync":
                self._sync()
                # Release the copied ring and 4 KiB buffer before verification.
                job["rows"] = None
                job["state"] = "verify-prefix"
            elif state == "verify-prefix":
                job["stream"] = open(job["path"], "rb")
                header, prefix = self._header(job["stream"])
                if prefix != job["prefix"] or header != job["header"]:
                    raise OSError("checkpoint verification failed")
                job["crc"] = _crc32(prefix)
                job["completed"] = 0
                job["state"] = "verify-payload"
                self._progress("verify", 0, job["total"])
            elif state == "verify-payload":
                remaining = job["total"] - job["completed"]
                if remaining:
                    chunk = _read_exact(job["stream"], min(remaining, job["chunk_bytes"]))
                    job["crc"] = _crc32(chunk, job["crc"])
                    job["completed"] += len(chunk)
                    self._progress("verify", job["completed"], job["total"])
                else:
                    job["state"] = "verify-footer"
            elif state == "verify-footer":
                expected = struct.unpack("<I", _read_exact(job["stream"], 4))[0]
                if expected != job["crc"] or job["stream"].read(1):
                    raise OSError("checkpoint verification failed")
                self._close_save_stream(job)
                self._current_path = job["path"]
                self._generation = job["header"]["generation"]
                self._save_job = None
                self._status("saved")
                self._progress("verify", job["total"], job["total"])
                return True
            return None
        except (
            OSError, ValueError, TypeError, AttributeError, OverflowError,
            RuntimeError, MemoryError
        ) as error:
            self._save_job = None
            try:
                self._close_save_stream(job)
            except (OSError, RuntimeError):
                pass
            self._status("save unavailable: " + str(error), error=True)
            return False
        finally:
            self._servicing_save = False

    def save(self, history, preferences, saved_at_epoch, now_monotonic=None):
        """Synchronous compatibility API for replay, recovery tools and tests."""
        if not self._begin_save(
            history, preferences, saved_at_epoch, now_monotonic, _CHUNK_BYTES
        ):
            return False
        while self.saving:
            result = self.service_save()
            if result is not None:
                return result
        return False

    def save_preferences(self, preferences):
        """Save settings independently, preserving history even without a UTC clock.

        The application should coalesce button changes and rate-limit calls to
        avoid unnecessary flash writes. Revision numbers are shared with history
        snapshots, so a newer standalone settings change wins during restoration.
        An active history job defers this call (False, without writing). Keep the
        application's preference-dirty flag set and retry after that job ends.
        """
        if self.saving or self._servicing_save:
            self._status("preferences deferred: history save active")
            return False
        try:
            clean = _preferences(preferences)
            if not self._history_checked:
                self._select_latest()
            if not self._preferences_checked:
                self._select_latest_preferences()
            generation = max(self._generation, self._preferences_generation) + 1
            if generation > 0x7FFFFFFF:
                raise ValueError("checkpoint generation exhausted")
            slot = "b" if self._preferences_current_path == self._path("a", True) else "a"
            path = self._path(slot, True)
            header = {
                "version": 1, "schema": "preferences-v1", "generation": generation,
                "preferences": clean,
            }
            raw = json.dumps(header).encode("utf-8")
            prefix = _PREFERENCES_MAGIC + struct.pack("<H", len(raw)) + raw
            self._ensure_directory()
            with open(path, "wb") as stream:
                _write_all(stream, prefix)
                _write_all(stream, struct.pack("<I", _crc32(prefix)))
                self._progress("preferences", len(prefix), len(prefix))
            self._sync()
            if self._read_slot(path, preferences=True) is None:
                raise OSError("preferences verification failed")
            self._preferences_current_path = path
            self._preferences_generation = generation
            self._status("preferences saved")
            return True
        except (
            OSError, ValueError, TypeError, AttributeError, OverflowError, RuntimeError
        ) as error:
            self._status("preferences save unavailable: " + str(error), error=True)
            return False

    def peek_preferences(self):
        """Read the newest complete snapshot's preferences without restoring history."""
        selected = self._select_latest()
        prefs_selected = self._select_latest_preferences()
        preferences = self._merged_preferences(
            selected["header"] if selected else None, prefs_selected
        )
        if preferences is None:
            self._status("no valid checkpoint")
            return None
        self._status("preferences available")
        return preferences

    def _clear_history(self, history, now_monotonic):
        history.write_index = 0
        history.data_count = 0
        history._origin = now_monotonic
        history._last_slot = -1
        history._last_timestamp = None

    def _restore_rows(self, selected, history, gap_slots):
        """Recheck CRC on the restore pass; clear any partial restore on failure."""
        header = selected["header"]
        retained = min(header["count"], self.max_samples, history.max_samples - gap_slots)
        skip_rows = header["count"] - retained
        with open(selected["path"], "rb") as stream:
            prefix = _read_exact(stream, len(selected["prefix"]))
            if prefix != selected["prefix"]:
                raise ValueError("checkpoint changed during restore")
            crc = _crc32(prefix)
            rows_seen = 0
            remaining = header["count"] * 8
            total = remaining
            self._progress("restore", 0, total)
            while remaining:
                chunk = _read_exact(stream, min(remaining, _CHUNK_BYTES))
                crc = _crc32(chunk, crc)
                for offset in range(0, len(chunk), 8):
                    if rows_seen >= skip_rows:
                        values = struct.unpack_from("<hhhh", chunk, offset)
                        for series, value in zip(history._series, values):
                            series[history.write_index] = value
                        history.write_index = (history.write_index + 1) % history.max_samples
                        history.data_count += 1
                    rows_seen += 1
                remaining -= len(chunk)
                self._progress("restore", total - remaining, total)
            if struct.unpack("<I", _read_exact(stream, 4))[0] != crc or stream.read(1):
                raise ValueError("checkpoint changed during restore")
        for _ in range(gap_slots):
            history._append((None, None, None, None))
        return retained

    def load_archive(self, history):
        """Read a prior session into a SEPARATE buffer for an archival UI view.

        No wall-clock gap or freshness is inferred. This buffer must never feed
        live pressure trends, NowCast, alerts or sampling. Its recorded interval
        is preserved for graph durations, even if current settings changed.
        The caller owns this buffer and treats it as read-only after restoration.
        """
        self._clear_history(history, 0)
        selected = self._select_latest()
        if selected is None:
            self._status("no valid history archive")
            return None
        header = selected["header"]
        prefs_selected = self._select_latest_preferences()
        previous_interval = history.interval_seconds
        try:
            restored = self._restore_rows(selected, history, 0)
            history.interval_seconds = header["interval"]
            history._origin = 0
            history._last_slot = history.data_count - 1
            history._last_timestamp = (
                history._last_slot * history.interval_seconds if history.data_count else None
            )
            self._status("previous session archived; not live data")
            return {
                "preferences": self._merged_preferences(header, prefs_selected),
                "saved_at_epoch": header["saved_at_epoch"],
                "latest_slot_epoch": header["latest_slot_epoch"],
                "latest_slot_fraction": header.get("latest_slot_fraction", 0),
                "clock_trusted": False, "archived": True,
                "restored_samples": restored, "missing_slots": 0,
                "generation": header["generation"], "interval_seconds": header["interval"],
                "info": self.status,
            }
        except (
            OSError, ValueError, TypeError, AttributeError, OverflowError, RuntimeError
        ) as error:
            self._clear_history(history, 0)
            history.interval_seconds = previous_interval
            self._status("archive restore unavailable: " + str(error), error=True)
            return None

    def load(self, history, now_epoch=None, now_monotonic=0):
        """Restore settings and time-aligned history, marking elapsed slots missing.

        The result includes preferences, saved_at_epoch, clock_trusted,
        restored_samples, missing_slots, generation and a readable info string.
        Unknown/backwards clocks and changed intervals start an empty history.
        Ring time is rebased to the new process's monotonic clock, so the first
        current reading replaces its own current slot and future readings append.
        """
        if not _number(now_monotonic, 0, 1e12):
            self._status("load unavailable: invalid monotonic time", error=True)
            return None
        self._clear_history(history, now_monotonic)
        selected = self._select_latest()
        prefs_selected = self._select_latest_preferences()
        preferences = self._merged_preferences(
            selected["header"] if selected else None, prefs_selected
        )
        if selected is None:
            if preferences is not None:
                self._status("preferences restored; no valid history checkpoint")
                return {
                    "preferences": preferences, "saved_at_epoch": None,
                    "clock_trusted": _epoch(now_epoch), "restored_samples": 0,
                    "missing_slots": 0, "generation": prefs_selected["header"]["generation"],
                    "info": self.status,
                }
            self._status("no valid checkpoint")
            return None
        header = selected["header"]
        result = {
            "preferences": preferences,
            "saved_at_epoch": header["saved_at_epoch"], "clock_trusted": False,
            "restored_samples": 0, "missing_slots": 0,
            "generation": header["generation"], "info": "preferences restored; clock untrusted",
        }
        newest = header["latest_slot_epoch"]
        if header["count"] == 0 and _epoch(now_epoch) and _epoch(header["saved_at_epoch"]):
            result["clock_trusted"] = now_epoch >= header["saved_at_epoch"]
            result["info"] = "preferences restored; no saved history"
            self._status(result["info"])
            return result
        if not _epoch(now_epoch) or not _epoch(newest):
            self._status(result["info"])
            return result
        if now_epoch < header["saved_at_epoch"]:
            result["info"] = "preferences restored; UTC clock moved backwards"
            self._status(result["info"])
            return result
        result["clock_trusted"] = True
        if (
            header["interval"] != self.interval_seconds
            or history.interval_seconds != self.interval_seconds
        ):
            result["info"] = "preferences restored; sampling interval changed"
            self._status(result["info"])
            return result
        # Both large operands remain integers; only a small elapsed duration is
        # converted to float when the fractional-slot correction is applied.
        age = int(now_epoch) - int(newest) - header.get("latest_slot_fraction", 0)
        gap_slots = int(age / self.interval_seconds)
        if gap_slots >= min(self.max_samples, history.max_samples):
            result["info"] = "preferences restored; history expired"
            self._status(result["info"])
            return result
        try:
            result["restored_samples"] = self._restore_rows(selected, history, gap_slots)
            result["missing_slots"] = gap_slots
            # The last saved sample is slot zero; imported elapsed slots end at
            # floor(age / interval), preserving fractional-slot phase exactly.
            history._origin = now_monotonic - age
            history._last_slot = gap_slots
            history._last_timestamp = now_monotonic
            result["info"] = "history and preferences restored"
            self._status(result["info"])
            return result
        except (
            OSError, ValueError, TypeError, AttributeError, OverflowError, RuntimeError
        ) as error:
            self._clear_history(history, now_monotonic)
            result["restored_samples"] = 0
            result["info"] = "preferences restored; history restore failed"
            self._status(result["info"] + ": " + str(error), error=True)
            return result
