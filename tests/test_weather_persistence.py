import binascii
import builtins
import json
import random
import struct

import pytest

from weather_core import DataHistory
import weather_persistence as persistence
from weather_persistence import CheckpointStore


EPOCH = 1791540000
PREFERENCES = {
    "graph_index": 2, "resolution_index": 4,
    "brightness_index": 1, "temperature_unit": "C",
}


def history(capacity=12, interval=60, count=4):
    result = DataHistory(capacity, interval, start_time=100)
    for index in range(count):
        result.add_reading(
            index + 0.1, 1000 + index * 0.1,
            -10 + index * 0.1, None if index % 2 else index,
            100 + index * interval,
        )
    return result


def values(data):
    return [
        tuple(data.value_ago(channel, ago) for channel in range(4))
        for ago in range(data.data_count - 1, -1, -1)
    ]


def store(tmp_path, capacity=12, interval=60):
    return CheckpointStore(str(tmp_path), capacity, interval)


def rewrite_header(path, changes):
    blob = path.read_bytes()
    length = struct.unpack("<H", blob[8:10])[0]
    header = json.loads(blob[10:10 + length])
    header.update(changes)
    raw = json.dumps(header).encode("utf-8")
    prefix = blob[:8] + struct.pack("<H", len(raw)) + raw
    payload = blob[10 + length:-4]
    body = prefix + payload
    path.write_bytes(body + struct.pack("<I", binascii.crc32(body)))


def test_all_four_series_roundtrip_preserves_order_and_none(tmp_path):
    original = history()
    checkpoint = store(tmp_path)
    assert checkpoint.save(original, PREFERENCES, EPOCH, now_monotonic=280)
    restored = DataHistory(12)
    result = checkpoint.load(restored, EPOCH, 500)
    assert values(restored) == values(original)
    assert result["preferences"] == PREFERENCES
    assert result["restored_samples"] == 4
    assert result["missing_slots"] == 0
    assert result["clock_trusted"]
    assert checkpoint.status == "history and preferences restored"
    assert checkpoint.peek_preferences() == PREFERENCES


def test_wrapped_ring_and_smaller_destination_preserve_newest_rows(tmp_path):
    original = history(capacity=5, count=9)
    checkpoint = store(tmp_path, capacity=5)
    assert checkpoint.save(original, PREFERENCES, EPOCH)
    destination = DataHistory(3)
    result = checkpoint.load(destination, EPOCH, 10)
    assert values(destination) == values(original)[-3:]
    assert result["restored_samples"] == 3


def test_save_capacity_limits_persisted_history(tmp_path):
    original = history(count=9)
    checkpoint = store(tmp_path, capacity=3)
    assert checkpoint.save(original, PREFERENCES, EPOCH)
    destination = DataHistory(12)
    assert checkpoint.load(destination, EPOCH)["restored_samples"] == 3
    assert values(destination) == values(original)[-3:]


def test_downtime_is_missing_and_new_process_continues(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(), PREFERENCES, EPOCH)
    destination = DataHistory(12)
    result = checkpoint.load(destination, EPOCH + 185, now_monotonic=0)
    assert result["restored_samples"] == 4
    assert result["missing_slots"] == 3
    assert destination.data_count == 7
    assert values(destination)[-3:] == [(None,) * 4] * 3
    # The startup measurement occupies the current missing slot, rather than
    # inventing a further minute. The subsequent sampling schedule can start
    # immediately and continue at 60 seconds in the new monotonic timebase.
    destination.add_reading(10, 1010, 50, 70, 0)
    assert destination.data_count == 7
    assert destination.value_ago(0) == 10
    destination.add_reading(11, 1011, 51, 71, 60)
    assert destination.data_count == 8
    assert destination.value_ago(0, 1) == 10
    assert destination.value_ago(0) == 11


def test_fractional_slot_phase_is_preserved_between_checkpoints(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(), PREFERENCES, EPOCH, now_monotonic=320)
    destination = DataHistory(12)
    result = checkpoint.load(destination, EPOCH + 10, 20)
    assert result["missing_slots"] == 0  # saved sample is only 50 seconds old
    destination.add_reading(8, 1000, 20, 30, 29)
    assert destination.data_count == 4
    destination.add_reading(9, 1000, 20, 30, 30)
    assert destination.data_count == 5


def test_large_unix_epochs_remain_integer_with_subsecond_slot_phase(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(), PREFERENCES, EPOCH, now_monotonic=339.25)
    blob = (tmp_path / "checkpoint-a.bin").read_bytes()
    length = struct.unpack("<H", blob[8:10])[0]
    header = json.loads(blob[10:10 + length])
    assert isinstance(header["saved_at_epoch"], int)
    assert isinstance(header["latest_slot_epoch"], int)
    assert header["latest_slot_epoch"] == EPOCH - 60
    assert header["latest_slot_fraction"] == pytest.approx(0.75)
    destination = DataHistory(12)
    assert checkpoint.load(destination, EPOCH, 0)["missing_slots"] == 0
    destination.add_reading(8, 1000, 20, 30, 0.74)
    assert destination.data_count == 4
    destination.add_reading(9, 1000, 20, 30, 0.75)
    assert destination.data_count == 5


@pytest.mark.parametrize("clock", [None, 0, 946684800, float("nan"), EPOCH - 1])
def test_unknown_or_backwards_clock_restores_preferences_only(tmp_path, clock):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(), PREFERENCES, EPOCH)
    destination = history(count=2)
    result = checkpoint.load(destination, clock, 10)
    assert result["preferences"] == PREFERENCES
    assert not result["clock_trusted"]
    assert result["restored_samples"] == destination.data_count == 0
    destination.add_reading(3, 1000, 20, 30, 10)
    assert destination.data_count == 1


def test_save_without_trusted_clock_keeps_preferences_without_guessing(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(), PREFERENCES, None)
    destination = DataHistory(12)
    result = checkpoint.load(destination, EPOCH)
    assert result["saved_at_epoch"] is None
    assert result["restored_samples"] == destination.data_count == 0
    assert result["preferences"] == PREFERENCES


def test_retention_expiry_does_not_resurrect_stale_measurements(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(), PREFERENCES, EPOCH)
    destination = DataHistory(12)
    result = checkpoint.load(destination, EPOCH + 12 * 60)
    assert destination.data_count == result["restored_samples"] == 0
    assert "expired" in result["info"]


def test_changed_interval_preserves_preferences_and_clears_history(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(), PREFERENCES, EPOCH)
    updated = store(tmp_path, interval=120)
    destination = DataHistory(12, interval_seconds=120)
    result = updated.load(destination, EPOCH, 0)
    assert destination.data_count == 0
    assert "interval changed" in result["info"]
    assert result["preferences"] == PREFERENCES


@pytest.mark.parametrize("corruption", ["truncate", "header", "payload", "footer", "extra"])
def test_invalid_newest_slot_falls_back_to_prior_complete_slot(tmp_path, corruption):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(count=1), PREFERENCES, EPOCH)
    assert checkpoint.save(history(count=2), dict(PREFERENCES, graph_index=1), EPOCH)
    path = tmp_path / "checkpoint-b.bin"
    data = bytearray(path.read_bytes())
    if corruption == "truncate":
        data = data[:-5]
    elif corruption == "extra":
        data += b"junk"
    else:
        length = struct.unpack("<H", data[8:10])[0]
        position = {"header": 30, "payload": 10 + length, "footer": len(data) - 1}[corruption]
        data[position] ^= 1
    path.write_bytes(data)
    destination = DataHistory(12)
    result = store(tmp_path).load(destination, EPOCH)
    assert result["generation"] == 1
    assert result["preferences"] == PREFERENCES
    assert destination.data_count == 1


@pytest.mark.parametrize("changes", [
    {"version": 2}, {"version": True}, {"schema": "unknown"}, {"capacity": 0},
    {"capacity": 65536}, {"count": -1}, {"count": 13},
    {"count": True}, {"generation": 0}, {"interval": 0},
    {"latest_slot_epoch": EPOCH + 1},
    {"preferences": dict(PREFERENCES, brightness_index=-1)},
    {"preferences": dict(PREFERENCES, temperature_unit="K")},
])
def test_authenticated_but_invalid_schema_or_bounds_rejected(tmp_path, changes):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(), PREFERENCES, EPOCH)
    rewrite_header(tmp_path / "checkpoint-a.bin", changes)
    destination = DataHistory(12)
    assert checkpoint.load(destination, EPOCH) is None
    assert destination.data_count == 0


def test_crc_fallback_matches_native_and_streaming(monkeypatch):
    data = bytes(range(256)) * 8
    monkeypatch.setattr(persistence, "_native_crc32", None)
    assert persistence._crc32(b"123456789") == 0xCBF43926
    assert persistence._crc32(data) == binascii.crc32(data)
    running = 0
    for offset in range(0, len(data), 67):
        running = persistence._crc32(data[offset:offset + 67], running)
    assert running == binascii.crc32(data)


def test_multiple_saves_alternate_slots_and_survive_new_store(tmp_path):
    checkpoint = store(tmp_path)
    for count in range(1, 5):
        assert checkpoint.save(history(count=count), PREFERENCES, EPOCH)
    assert len(list(tmp_path.glob("*.bin"))) == 2
    destination = DataHistory(12)
    assert store(tmp_path).load(destination, EPOCH)["generation"] == 4
    assert destination.data_count == 4


def test_fault_during_write_preserves_prior_snapshot_in_same_process(tmp_path, monkeypatch):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(count=1), PREFERENCES, EPOCH)
    real_write = persistence._write_all
    calls = 0

    def failing_write(stream, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            stream.write(bytes(data)[:3])
            raise OSError("simulated power loss")
        real_write(stream, data)

    with monkeypatch.context() as patch:
        patch.setattr(persistence, "_write_all", failing_write)
        assert not checkpoint.save(history(count=2), PREFERENCES, EPOCH)
    destination = DataHistory(12)
    assert checkpoint.load(destination, EPOCH)["generation"] == 1
    assert destination.data_count == 1
    assert checkpoint.save(history(count=3), PREFERENCES, EPOCH)
    assert checkpoint.load(destination, EPOCH)["generation"] == 2
    assert destination.data_count == 3


def test_read_only_disk_failure_is_throttled_and_nonfatal(tmp_path, monkeypatch, capsys):
    checkpoint = store(tmp_path)
    real_open = builtins.open

    def readonly_open(path, mode="r", *args, **kwargs):
        if "w" in mode:
            raise OSError("read-only filesystem")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", readonly_open)
    for _ in range(3):
        assert not checkpoint.save(history(), PREFERENCES, EPOCH)
    assert "read-only" in checkpoint.status
    assert capsys.readouterr().out.count("Checkpoint:") == 1


def test_missing_directory_is_created_and_invalid_input_never_clobbers(tmp_path):
    checkpoint = store(tmp_path / "state")
    assert checkpoint.save(history(), PREFERENCES, EPOCH)
    before = (tmp_path / "state" / "checkpoint-a.bin").read_bytes()
    assert not checkpoint.save(history(), dict(PREFERENCES, temperature_unit="K"), EPOCH)
    assert not checkpoint.save(history(interval=120), PREFERENCES, EPOCH)
    assert (tmp_path / "state" / "checkpoint-a.bin").read_bytes() == before


def test_week_checkpoint_io_is_bounded_and_can_use_fallback_crc(tmp_path, monkeypatch):
    checkpoint = store(tmp_path, capacity=10080)
    original = history(capacity=10080, count=10080)
    largest_chunk = 0
    real_write = persistence._write_all

    def tracked_write(stream, data):
        nonlocal largest_chunk
        largest_chunk = max(largest_chunk, len(data))
        real_write(stream, data)

    monkeypatch.setattr(persistence, "_native_crc32", None)
    monkeypatch.setattr(persistence, "_write_all", tracked_write)
    assert checkpoint.save(original, PREFERENCES, EPOCH)
    assert largest_chunk <= persistence._CHUNK_BYTES
    assert (tmp_path / "checkpoint-a.bin").stat().st_size < 82000
    destination = DataHistory(10080)
    result = checkpoint.load(destination, EPOCH + 125, 10)
    assert result["missing_slots"] == 2
    assert result["restored_samples"] == 10078
    assert destination.value_ago(0, 2) == original.value_ago(0)
    assert destination.data_count == 10080


def test_second_pass_detects_file_changed_after_validation(tmp_path, monkeypatch):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(), PREFERENCES, EPOCH)
    real_select = checkpoint._select_latest

    def changed_file():
        selected = real_select()
        path = tmp_path / "checkpoint-a.bin"
        blob = bytearray(path.read_bytes())
        blob[-5] ^= 1
        path.write_bytes(blob)
        return selected

    monkeypatch.setattr(checkpoint, "_select_latest", changed_file)
    destination = DataHistory(12)
    result = checkpoint.load(destination, EPOCH)
    assert result["preferences"] == PREFERENCES
    assert result["restored_samples"] == destination.data_count == 0
    assert "failed" in result["info"]


def test_standalone_preferences_survive_untrusted_clock_and_missing_history(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save_preferences(PREFERENCES)
    assert list(tmp_path.glob("checkpoint-*.bin")) == []
    destination = DataHistory(12)
    result = store(tmp_path).load(destination, None)
    assert result["preferences"] == PREFERENCES
    assert result["restored_samples"] == destination.data_count == 0
    assert not result["clock_trusted"]
    assert "no valid history" in result["info"]
    assert store(tmp_path).peek_preferences() == PREFERENCES


def test_standalone_preferences_never_overwrite_history_without_clock(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(), PREFERENCES, EPOCH)
    original = (tmp_path / "checkpoint-a.bin").read_bytes()
    latest = dict(PREFERENCES, temperature_unit="F")
    assert checkpoint.save_preferences(latest)
    assert (tmp_path / "checkpoint-a.bin").read_bytes() == original
    destination = DataHistory(12)
    result = store(tmp_path).load(destination, None)
    assert result["preferences"] == latest
    assert destination.data_count == 0
    assert store(tmp_path).load(destination, EPOCH)["restored_samples"] == 4


def test_shared_preference_revisions_choose_latest_history_or_standalone(tmp_path):
    checkpoint = store(tmp_path)
    first = dict(PREFERENCES, graph_index=0)
    second = dict(PREFERENCES, graph_index=1)
    third = dict(PREFERENCES, graph_index=2)
    assert checkpoint.save(history(), first, EPOCH)
    assert checkpoint.save_preferences(second)
    assert store(tmp_path).peek_preferences() == second
    assert checkpoint.save(history(), third, EPOCH)
    assert store(tmp_path).peek_preferences() == third
    # New processes discover both revision counters before writing either kind.
    assert store(tmp_path).save_preferences(first)
    assert store(tmp_path).peek_preferences() == first
    assert store(tmp_path).save(history(), second, EPOCH)
    assert store(tmp_path).peek_preferences() == second


def test_corrupt_standalone_preferences_fall_back_to_prior_slot(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save_preferences(PREFERENCES)
    assert checkpoint.save_preferences(dict(PREFERENCES, graph_index=1))
    path = tmp_path / "preferences-b.bin"
    path.write_bytes(path.read_bytes()[:-2])
    assert store(tmp_path).peek_preferences() == PREFERENCES


def test_failure_writing_standalone_preferences_keeps_last_complete_slot(tmp_path, monkeypatch):
    checkpoint = store(tmp_path)
    assert checkpoint.save_preferences(PREFERENCES)
    real_write = persistence._write_all

    def fail_footer(stream, data):
        if len(data) == 4:
            raise OSError("simulated preferences power loss")
        real_write(stream, data)

    with monkeypatch.context() as patch:
        patch.setattr(persistence, "_write_all", fail_footer)
        assert not checkpoint.save_preferences(dict(PREFERENCES, temperature_unit="F"))
    assert store(tmp_path).peek_preferences() == PREFERENCES


def test_empty_history_can_checkpoint_preferences(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(DataHistory(12), PREFERENCES, EPOCH)
    destination = DataHistory(12)
    result = checkpoint.load(destination, EPOCH)
    assert result["preferences"] == PREFERENCES
    assert destination.data_count == 0


def test_history_with_invalid_source_bounds_is_nonfatal(tmp_path):
    checkpoint = store(tmp_path)
    original = history()
    original.data_count = 100
    assert not checkpoint.save(original, PREFERENCES, EPOCH)
    assert list(tmp_path.glob("*.bin")) == []


def test_downtime_breaks_pressure_trend_instead_of_treating_old_values_as_current(tmp_path):
    original = DataHistory(720)
    for minute in range(241):
        original.add_reading(1, 1000, 70, 50, minute * 60)
    assert original.pressure_trend() == "steady"
    checkpoint = store(tmp_path, 720)
    assert checkpoint.save(original, PREFERENCES, EPOCH)
    destination = DataHistory(720)
    assert checkpoint.load(destination, EPOCH + 600)["missing_slots"] == 10
    destination.add_reading(1, 1000, 70, 50, 0)
    assert destination.pressure_trend() == "-"


def test_unknown_clock_archive_preserves_prior_session_without_polluting_live_history(tmp_path):
    checkpoint = store(tmp_path)
    original = history()
    assert checkpoint.save(original, PREFERENCES, None)
    live = DataHistory(12)
    result = checkpoint.load(live, None)
    assert live.data_count == result["restored_samples"] == 0
    archive = DataHistory(12)
    result = checkpoint.load_archive(archive)
    assert result["archived"]
    assert not result["clock_trusted"]
    assert result["saved_at_epoch"] is result["latest_slot_epoch"] is None
    assert values(archive) == values(original)
    assert live.data_count == 0
    live.add_reading(5, 1000, 20, 50, 0)
    assert live.data_count == 1
    assert values(archive) == values(original)


def test_archive_uses_recorded_interval_even_after_setting_change(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(), PREFERENCES, EPOCH)
    new_checkpoint = store(tmp_path, interval=120)
    archive = DataHistory(3, interval_seconds=120)
    result = new_checkpoint.load_archive(archive)
    assert result["interval_seconds"] == archive.interval_seconds == 60
    assert result["restored_samples"] == archive.data_count == 3
    assert archive.value_ago(0) == 3.1
    assert result["saved_at_epoch"] == EPOCH


def test_archive_corrupt_latest_falls_back_without_guessing_time(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(count=1), PREFERENCES, EPOCH)
    assert checkpoint.save(history(count=2), PREFERENCES, None)
    path = tmp_path / "checkpoint-b.bin"
    path.write_bytes(path.read_bytes()[:-5])
    archive = DataHistory(12)
    result = checkpoint.load_archive(archive)
    assert result["generation"] == 1
    assert result["saved_at_epoch"] == EPOCH
    assert archive.data_count == 1
    assert result["archived"] and not result["clock_trusted"]


def test_subsequent_untrusted_session_checkpoints_remain_archival(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(count=4), PREFERENCES, EPOCH)
    assert checkpoint.save(history(count=1), PREFERENCES, None)
    assert checkpoint.save(history(count=2), PREFERENCES, None)
    live = DataHistory(12)
    assert checkpoint.load(live, EPOCH)["restored_samples"] == 0
    archive = DataHistory(12)
    result = checkpoint.load_archive(archive)
    assert result["restored_samples"] == 2
    assert result["saved_at_epoch"] is None
    assert live.data_count == 0


def test_crc_small_integer_lanes_match_native_for_all_bytes_and_prior_states(monkeypatch):
    monkeypatch.setattr(persistence, "_native_crc32", None)
    randomizer = random.Random(932)
    data = bytes(randomizer.randrange(256) for _ in range(8192))
    for previous in (0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF):
        assert persistence._crc32(data, previous) == binascii.crc32(data, previous)
    assert persistence._crc32(b"", 0xABCD9876) == 0xABCD9876
    low, high = persistence._crc_tables()
    assert len(low) == len(high) == 256
    assert low.itemsize * len(low) + high.itemsize * len(high) == 1024
    assert all(0 <= value <= 65535 for value in low)
    assert all(0 <= value <= 65535 for value in high)


def test_progress_callback_follows_completed_bounded_write_and_verify_chunks(tmp_path):
    events = []
    chunk_bytes = persistence._CHUNK_BYTES
    count = chunk_bytes // 8 * 2 + 2
    checkpoint = store(tmp_path, capacity=count + 2)
    checkpoint.progress_callback = lambda: events.append((
        checkpoint.progress_phase, checkpoint.progress_bytes, checkpoint.progress_total
    ))
    original = history(capacity=count + 2, count=count)
    assert checkpoint.save(original, PREFERENCES, EPOCH)
    total_bytes = count * 8
    expected = [0, chunk_bytes, chunk_bytes * 2, total_bytes, total_bytes]
    for phase in ("write", "verify"):
        completed = [done for event_phase, done, total in events if event_phase == phase]
        assert completed == expected
        assert max(
            later - earlier for earlier, later in zip(completed, completed[1:])
        ) <= chunk_bytes
    assert events.index(("write", total_bytes, total_bytes)) < events.index((
        "verify", 0, total_bytes
    ))
    events.clear()
    restored = DataHistory(count + 2)
    assert checkpoint.load(restored, EPOCH)["restored_samples"] == count
    assert values(restored) == values(original)
    assert [done for phase, done, total in events if phase == "restore"] == expected[:-1]


def test_progress_is_not_reported_for_failed_or_pending_write(tmp_path, monkeypatch):
    checkpoint = store(tmp_path)
    events = []
    checkpoint.progress_callback = lambda: events.append(checkpoint.progress_phase)

    def failed_write(stream, data):
        assert events == []  # No watchdog feed is issued before this I/O returns.
        raise OSError("blocked or failed storage")

    monkeypatch.setattr(persistence, "_write_all", failed_write)
    assert not checkpoint.save(history(), PREFERENCES, EPOCH)
    assert events == []


def test_progress_callback_can_be_installed_at_construction_and_after_startup(tmp_path):
    calls = []
    checkpoint = CheckpointStore(str(tmp_path), 12, 60, progress_callback=lambda: calls.append(1))
    assert checkpoint.save_preferences(PREFERENCES)
    assert calls
    checkpoint.progress_callback = None
    count = len(calls)
    assert checkpoint.save_preferences(PREFERENCES)
    assert len(calls) == count
    with pytest.raises(ValueError, match="progress callback"):
        CheckpointStore(str(tmp_path), progress_callback=1)


def finish_save(checkpoint, between_steps=None):
    """Model an application tick doing other work between cooperative steps."""
    result = None
    ticks = 0
    while checkpoint.saving:
        if between_steps is not None:
            between_steps(ticks)
        result = checkpoint.service_save()
        ticks += 1
        assert ticks < 3000
    return result, ticks


def test_cooperative_save_freezes_values_metadata_and_preferences_while_sampling(tmp_path):
    original = history(capacity=128, count=128)
    expected = values(original)
    prefs = dict(PREFERENCES)
    checkpoint = store(tmp_path, capacity=128)
    assert checkpoint.start_save(original, prefs, EPOCH, original._last_timestamp)
    assert checkpoint.saving
    assert list(tmp_path.glob("*.bin")) == []  # Queuing does not write storage.
    prefs["temperature_unit"] = "F"
    button_events = []

    def tick(index):
        button_events.append(index)
        original.add_reading(
            123, 1100, 80, 40, original._last_timestamp + 60
        )

    result, ticks = finish_save(checkpoint, tick)
    assert result is True
    assert ticks == len(button_events) and ticks > 10
    assert values(original) != expected
    restored = DataHistory(128)
    info = checkpoint.load(restored, EPOCH)
    assert values(restored) == expected
    assert info["preferences"] == PREFERENCES
    assert info["missing_slots"] == 0
    assert checkpoint.service_save() is None


def test_cooperative_week_payload_is_one_small_chunk_per_service_call(tmp_path, monkeypatch):
    original = history(capacity=10080, count=10080)
    checkpoint = store(tmp_path, capacity=10080)
    writes = []
    completed = []
    real_write = persistence._write_all

    def tracked_write(stream, data):
        writes.append(len(data))
        real_write(stream, data)

    monkeypatch.setattr(persistence, "_write_all", tracked_write)
    monkeypatch.setattr(persistence, "_native_crc32", None)
    checkpoint.progress_callback = lambda: completed.append((
        checkpoint.progress_phase, checkpoint.progress_bytes
    ))
    assert checkpoint.start_save(original, PREFERENCES, EPOCH)
    calls = 0
    while checkpoint.saving:
        before = len(writes)
        checkpoint.service_save()
        assert len(writes) - before <= 1
        calls += 1
    assert checkpoint.status == "saved"
    assert calls > 2 * original.data_count * 8 // persistence._SERVICE_BYTES
    assert max(writes) <= persistence._SERVICE_BYTES
    for phase in ("write", "verify"):
        positions = [done for current, done in completed if current == phase]
        assert positions[0] == 0
        assert positions[-1] == original.data_count * 8
        assert max(
            later - earlier for earlier, later in zip(positions, positions[1:])
        ) <= persistence._SERVICE_BYTES
    restored = DataHistory(10080)
    assert checkpoint.load(restored, EPOCH)["restored_samples"] == 10080
    assert values(restored) == values(original)


@pytest.mark.parametrize("state", [
    "prefix", "payload", "footer", "close", "sync", "verify-prefix",
    "verify-payload", "verify-footer",
])
def test_cancelling_any_pending_phase_keeps_prior_committed_checkpoint(tmp_path, state):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(count=1), PREFERENCES, EPOCH)
    prior = (tmp_path / "checkpoint-a.bin").read_bytes()
    assert checkpoint.start_save(history(count=4), PREFERENCES, EPOCH)
    for _ in range(50):
        if checkpoint._save_job["state"] == state:
            break
        assert checkpoint.service_save() is None
    else:
        pytest.fail("phase was not reached")
    stream = checkpoint._save_job["stream"]
    assert checkpoint.cancel_save()
    assert not checkpoint.saving
    assert stream is None or stream.closed
    assert not checkpoint.cancel_save()
    assert checkpoint.service_save() is None
    assert (tmp_path / "checkpoint-a.bin").read_bytes() == prior
    restored = DataHistory(12)
    assert store(tmp_path).load(restored, EPOCH)["generation"] == 1
    assert restored.data_count == 1
    assert checkpoint.save(history(count=2), PREFERENCES, EPOCH)
    assert checkpoint.load(restored, EPOCH)["generation"] == 2


def test_cooperative_write_failure_closes_stream_and_preserves_prior(tmp_path, monkeypatch):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(count=1), PREFERENCES, EPOCH)
    assert checkpoint.start_save(history(count=4), PREFERENCES, EPOCH)
    checkpoint.service_save()  # Header completes; payload is pending.
    stream = checkpoint._save_job["stream"]

    def partial_failure(stream, data):
        stream.write(bytes(data)[:3])
        raise OSError("simulated power loss")

    monkeypatch.setattr(persistence, "_write_all", partial_failure)
    assert checkpoint.service_save() is False
    assert stream.closed
    assert not checkpoint.saving
    restored = DataHistory(12)
    assert store(tmp_path).load(restored, EPOCH)["generation"] == 1
    assert restored.data_count == 1


def test_incremental_verification_detects_payload_changed_after_write(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(count=1), PREFERENCES, EPOCH)
    assert checkpoint.start_save(history(count=4), PREFERENCES, EPOCH)
    while checkpoint._save_job["state"] != "verify-prefix":
        assert checkpoint.service_save() is None
    path = tmp_path / "checkpoint-b.bin"
    blob = bytearray(path.read_bytes())
    blob[-5] ^= 1
    path.write_bytes(blob)
    result, _ = finish_save(checkpoint)
    assert result is False
    assert "verification failed" in checkpoint.status
    restored = DataHistory(12)
    assert store(tmp_path).load(restored, EPOCH)["generation"] == 1
    assert restored.data_count == 1


def test_truncated_incremental_verification_keeps_prior_checkpoint(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(count=1), PREFERENCES, EPOCH)
    assert checkpoint.start_save(history(count=4), PREFERENCES, EPOCH)
    while checkpoint._save_job["state"] != "verify-prefix":
        assert checkpoint.service_save() is None
    path = tmp_path / "checkpoint-b.bin"
    path.write_bytes(path.read_bytes()[:-5])
    result, _ = finish_save(checkpoint)
    assert result is False
    assert not checkpoint.saving
    restored = DataHistory(12)
    assert store(tmp_path).load(restored, EPOCH)["generation"] == 1


def test_preferences_deferred_during_job_receive_later_shared_generation(tmp_path):
    checkpoint = store(tmp_path)
    assert checkpoint.save(history(count=1), PREFERENCES, EPOCH)
    changed = dict(PREFERENCES, temperature_unit="F")
    assert checkpoint.start_save(history(count=4), PREFERENCES, EPOCH)
    assert not checkpoint.save_preferences(changed)
    assert checkpoint.status == "preferences deferred: history save active"
    assert not list(tmp_path.glob("preferences-*.bin"))
    assert not checkpoint.start_save(history(count=2), changed, EPOCH)
    assert not checkpoint.save(history(count=2), changed, EPOCH)
    assert finish_save(checkpoint)[0] is True
    assert checkpoint._generation == 2
    assert store(tmp_path).peek_preferences() == PREFERENCES
    assert checkpoint.save_preferences(changed)
    assert checkpoint._preferences_generation == 3
    assert store(tmp_path).peek_preferences() == changed
    assert checkpoint.start_save(history(count=2), changed, EPOCH)
    assert finish_save(checkpoint)[0] is True
    assert checkpoint._generation == 4


def test_startup_slot_scan_is_not_repeated_each_cooperative_save(tmp_path, monkeypatch):
    checkpoint = store(tmp_path)
    assert checkpoint.load(DataHistory(12), EPOCH) is None

    def unexpected_scan():
        raise AssertionError("startup slots must not be rescanned in the UI loop")

    monkeypatch.setattr(checkpoint, "_select_latest", unexpected_scan)
    monkeypatch.setattr(checkpoint, "_select_latest_preferences", unexpected_scan)
    for count in (1, 2):
        assert checkpoint.start_save(history(count=count), PREFERENCES, EPOCH)
        assert finish_save(checkpoint)[0] is True
    assert checkpoint.save_preferences(PREFERENCES)
