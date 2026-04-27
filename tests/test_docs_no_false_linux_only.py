"""Static lint: documentation must not claim AutoDev is Linux-only because of fcntl.

`fcntl` is POSIX and works on macOS. The Windows-specific fail message in
install.sh:73 is allowed because Windows native genuinely lacks fcntl —
the patterns below match only "fcntl ... Linux-only / Linux only" wording,
not the legitimate Windows-native rejection.

This guards SETUP.md, CLAUDE.md, README.md, and install.sh against
regression to the false claim.
"""
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

_FILES = [
    _REPO_ROOT / "SETUP.md",
    _REPO_ROOT / "CLAUDE.md",
    _REPO_ROOT / "README.md",
    _REPO_ROOT / "install.sh",
]

# "fcntl ... Linux-only" or "fcntl ... Linux only" within one sentence
# (the [^.\n]* prevents crossing sentence/line boundaries).
_BAD_FCNTL_LINUX_ONLY = re.compile(r"fcntl[^.\n]*Linux[- ]only", re.IGNORECASE)

# "Linux only (fcntl" specifically — catches CLAUDE.md:497 wording.
_BAD_LINUX_ONLY_FCNTL = re.compile(r"Linux only \(fcntl", re.IGNORECASE)


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_no_fcntl_linux_only_claim(path: Path):
    if not path.exists():
        pytest.skip(f"{path.name} not present in repo root")
    text = path.read_text()
    match = _BAD_FCNTL_LINUX_ONLY.search(text)
    assert match is None, (
        f"{path.name} contains a false 'fcntl ... Linux-only' claim "
        f"(matched: {match.group(0)!r}); fcntl is POSIX and works on macOS"
    )


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_no_linux_only_fcntl_parenthetical(path: Path):
    if not path.exists():
        pytest.skip(f"{path.name} not present in repo root")
    text = path.read_text()
    match = _BAD_LINUX_ONLY_FCNTL.search(text)
    assert match is None, (
        f"{path.name} contains a false 'Linux only (fcntl ...)' claim "
        f"(matched: {match.group(0)!r}); fcntl is POSIX and works on macOS"
    )
