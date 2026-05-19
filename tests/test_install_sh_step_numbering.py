"""Contract tests for install.sh step numbering (14 steps, no OpenClaw version gate)."""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _REPO_ROOT / "install.sh"


def _read_install_sh() -> str:
    return _INSTALL_SH.read_text(encoding="utf-8")


def test_header_declares_14_steps():
    text = _read_install_sh()
    assert "# install.sh — AutoDev interactive setup (14 steps)" in text
    assert "(15 steps)" not in text


def test_no_slash_15_step_markers():
    text = _read_install_sh()
    assert "/15" not in text, "install.sh must use /14 only after renumbering"


def test_exactly_14_hdr_steps_in_order():
    text = _read_install_sh()
    nums = [int(m.group(1)) for m in re.finditer(r'hdr "(\d+)/14\b', text)]
    assert nums == list(range(1, 15)), f"expected hdr 1/14..14/14 in order, got {nums}"


def test_version_check_removed():
    text = _read_install_sh()
    assert "OpenClaw version check" not in text
    assert "RECOMMENDED_OC_VERSION" not in text
    assert "OC_VERSION_STATUS" not in text
    assert "OpenClaw version:" not in text


def test_agent_workspace_follows_openclaw_detection():
    text = _read_install_sh()
    i4 = text.index("4/14  OPENCLAW DETECTION")
    i5 = text.index("5/14  AGENT WORKSPACE PROVISIONING")
    assert i4 < i5, "step 5 must immediately follow step 4 (no version block between)"

