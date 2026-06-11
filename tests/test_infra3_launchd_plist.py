"""Static lint for the macOS LaunchAgent plist at ui/com.autodev.ui.plist.

Mirrors tests/test_infra3_systemd_unit.py — both files (the systemd unit
and the launchd plist) must declare equivalent WorkingDirectory, executable,
restart policy, and log paths. CLAUDE.md documents this drift guard.

This test is host-agnostic: it parses the plist as XML/plist data and asserts
structural keys. It runs on Linux and macOS alike, so reintroducing drift
between the two service files fails CI on either platform.
"""
import os
import plistlib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
PLIST_FILE = str(_REPO_ROOT / "ui" / "com.autodev.ui.plist")


def test_plist_file_exists():
    """LaunchAgent plist must exist alongside ui/autodev-ui.service."""
    assert os.path.exists(PLIST_FILE), f"{PLIST_FILE} not found"


def test_plist_is_valid_xml():
    """File must parse as well-formed XML."""
    ET.parse(PLIST_FILE)


def test_plist_loads_as_dict():
    """plistlib.load must return a dict (the plist root)."""
    with open(PLIST_FILE, "rb") as f:
        plist = plistlib.load(f)
    assert isinstance(plist, dict), "plist root must be a dict"


def test_plist_label():
    """Label is the launchd-equivalent of the systemd unit Description."""
    with open(PLIST_FILE, "rb") as f:
        plist = plistlib.load(f)
    assert plist.get("Label") == "com.autodev.ui", (
        "Label must be 'com.autodev.ui'"
    )


def test_plist_program_arguments():
    """ProgramArguments must launch the server as a module: python3 -m ui.server.

    Mirrors test_infra3_systemd_unit.py: the script form ``python3 ui/server.py``
    dies with ModuleNotFoundError (package-absolute imports), so both service
    files must use ``-m ui.server`` resolved against WorkingDirectory.
    """
    with open(PLIST_FILE, "rb") as f:
        plist = plistlib.load(f)
    args = plist.get("ProgramArguments")
    assert isinstance(args, list), "ProgramArguments must be a list"
    assert len(args) >= 3, "ProgramArguments must have at least python + -m + module"
    assert args[-2:] == ["-m", "ui.server"], (
        f"ProgramArguments must end with ['-m', 'ui.server'], got {args[-2:]!r}"
    )


def test_plist_working_directory():
    """WorkingDirectory must be a non-empty string (placeholder is OK)."""
    with open(PLIST_FILE, "rb") as f:
        plist = plistlib.load(f)
    wd = plist.get("WorkingDirectory")
    assert isinstance(wd, str) and wd, "WorkingDirectory must be a non-empty string"


def test_plist_run_at_load():
    """RunAtLoad must be true so the agent starts when the plist is loaded."""
    with open(PLIST_FILE, "rb") as f:
        plist = plistlib.load(f)
    assert plist.get("RunAtLoad") is True, "RunAtLoad must be true"


def test_plist_keep_alive():
    """KeepAlive mirrors `Restart=on-failure` from the systemd unit. Either
    the bool form (always restart) or the dict form (e.g. {Crashed: True,
    SuccessfulExit: False}) is accepted."""
    with open(PLIST_FILE, "rb") as f:
        plist = plistlib.load(f)
    ka = plist.get("KeepAlive")
    assert ka is True or isinstance(ka, dict), (
        "KeepAlive must be either True or a dict (mirrors Restart=on-failure)"
    )


def test_plist_log_paths():
    """StandardOutPath and StandardErrorPath must be set for log capture."""
    with open(PLIST_FILE, "rb") as f:
        plist = plistlib.load(f)
    assert plist.get("StandardOutPath"), "StandardOutPath must be set"
    assert plist.get("StandardErrorPath"), "StandardErrorPath must be set"


def test_plist_has_install_documentation():
    """File must contain inline XML comments documenting launchctl install steps."""
    with open(PLIST_FILE) as f:
        content = f.read()
    assert "launchctl" in content.lower(), (
        "Missing launchctl documentation in inline comments"
    )
    assert "EDIT" in content or "placeholder" in content.lower(), (
        "WorkingDirectory should have a placeholder/EDIT comment"
    )
