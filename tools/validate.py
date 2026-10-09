"""Run from the project root: py -3 tools/validate.py."""

# Load Python's standard module before the CircuitPython launcher named code.py
# can shadow it when pytest adds this project to sys.path.
import code
import ast
import os
from pathlib import Path
import subprocess
import sys


def main():
    assert hasattr(code, "InteractiveConsole")
    project = Path(__file__).resolve().parents[1]
    for source in project.glob("*.py"):
        ast.parse(source.read_text(encoding="utf-8"), filename=str(source), feature_version=(3, 7))
    print("Python 3.7 syntax check passed for all device modules.", flush=True)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    import pytest

    result = pytest.main([str(project / "tests"), "-q"])
    if result:
        return result
    return subprocess.run(
        [sys.executable, "-m", "ruff", "check", str(project)], check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
