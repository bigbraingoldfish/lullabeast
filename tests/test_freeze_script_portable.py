"""Static lint: scripts/queue-e2e-strict-freeze.sh must not invoke `readlink -f`.

`readlink -f` is GNU-only; BSD `readlink` lacks `-f` until macOS Big Sur 11+.
The portable replacement is a one-line Python invocation:
    python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$PP"

The repo already requires Python 3.9+ (install.sh §2), so this is
dependency-neutral.
"""
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "queue-e2e-strict-freeze.sh"


def test_freeze_script_exists():
    assert _SCRIPT.is_file(), f"{_SCRIPT} missing"


def test_no_gnu_readlink_f():
    text = _SCRIPT.read_text()
    assert "readlink -f" not in text, (
        "`readlink -f` (GNU-only) found in queue-e2e-strict-freeze.sh — "
        "replace with `python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))'`"
    )
