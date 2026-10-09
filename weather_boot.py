# SPDX-License-Identifier: MIT
"""Exclusive filesystem ownership and a temporary edit-mode request.

The last 16 bytes of NVM hold a guarded one-shot request, written only during
deployment. The last 16 bytes of sleep memory describe this boot's live mode.
CircuitPython 7 on this MagTag clears sleep memory during hardware reset.
"""

MAGIC = b"MTWSV3\x00\x00"
CONTROL_SIZE = 16


def _memory():
    try:
        import alarm

        if len(alarm.sleep_memory) >= CONTROL_SIZE:
            return alarm.sleep_memory
    except (ImportError, AttributeError):
        pass
    return None


def editing_mode():
    memory = _memory()
    if memory is None:
        return False
    start = len(memory) - CONTROL_SIZE
    return bytes(memory[start : start + 8]) == MAGIC and memory[start + 9] == 1


def request_edit_mode(enabled=True):
    import microcontroller

    memory = microcontroller.nvm
    start = len(memory) - CONTROL_SIZE
    previous = bytes(memory[start:])
    if previous[:8] != MAGIC and not all(value in (0, 255) for value in previous):
        raise RuntimeError("Reserved edit flag overlaps existing NVM data; hold D at reset")
    requested = MAGIC + bytes((1 if enabled else 0,)) + bytes(7)
    if previous != requested:
        memory[start:] = requested


def configure_storage():
    import board
    import digitalio
    import storage
    import microcontroller

    button = digitalio.DigitalInOut(board.BUTTON_D)
    button.direction = digitalio.Direction.INPUT
    button.pull = digitalio.Pull.UP
    physical_edit = not button.value
    button.deinit()
    memory = _memory()
    nvm = microcontroller.nvm
    flag_start = len(nvm) - CONTROL_SIZE
    requested_edit = bytes(nvm[flag_start : flag_start + 8]) == MAGIC and nvm[flag_start + 8] == 1
    if requested_edit:
        nvm[flag_start + 8] = 0
    if memory is not None:
        start = len(memory) - CONTROL_SIZE
        memory[start : start + 8] = MAGIC
        memory[start + 8] = 0
        memory[start + 9] = 1 if requested_edit or physical_edit else 0
    edit = requested_edit or physical_edit
    # Never disable concurrent-write protection. Exactly one writer owns FAT.
    storage.remount("/", readonly=edit)
    print("Weather storage:", "computer editing" if edit else "device logging")
