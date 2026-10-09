"""Compare history allocation with an original code.py, without running its hardware code."""

import argparse
import ast
import gc
import json
from pathlib import Path
import sys
import tracemalloc


def original_history(source):
    tree = ast.parse(source.read_text(encoding="utf-8"))
    node = next(
        item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "DataHistory"
    )
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["DataHistory"]


def measure(factory, original=False):
    gc.collect()
    tracemalloc.start()
    history = factory()
    for index in range(10080):
        values = (1.0 + index % 51, 990.0 + index % 31, 60.0 + index % 21, 30.0 + index % 41)
        history.add_reading(*values) if original else history.add_reading(*values, index * 60)
    gc.collect()
    stored = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    if original:
        history.get_stats(0, 10080)
        series = history.get_series(0)
        window = series[-10080:]
        valid = [value for value in window if value is not None]
        assert valid
    else:
        summary = history.summarize(0, 10080, 258)
        assert summary[3] == 10080
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return {"stored_bytes": stored, "query_extra_peak_bytes": peak - stored}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from weather_core import DataHistory

    result = {
        "method": (
            "CPython tracemalloc, 10080 populated samples; not a CircuitPython heap measurement"
        ),
        "original": measure(original_history(args.original), original=True),
        "improved": measure(DataHistory),
        "device_history_payload_bytes": 80640,
    }
    output = json.dumps(result, indent=2)
    print(output)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
