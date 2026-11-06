"""install.sh in owned mode (the Docker entrypoint) must not hand out bare-metal steps.

The container entrypoint runs ``install.sh --owned-openclaw`` on every boot,
then restarts the gateway and starts and supervises the dashboard itself. The
installer's closing notes (start uvicorn by hand, verify the hooks with curl,
install a systemd unit or a LaunchAgent) and its "restart the gateway your
usual way" hint are bare-metal instructions: in a container's boot log they
read as steps the operator still has to take.
"""

from __future__ import annotations

import re
from pathlib import Path

_INSTALL_SH = (Path(__file__).resolve().parents[1] / "install.sh").read_text(encoding="utf-8")

_OWNED_GUARD = 'if [ "$OWNED_OPENCLAW" -ne 1 ]; then'


def test_closing_bare_metal_notes_skip_owned_mode():
    guard = _INSTALL_SH.rindex(_OWNED_GUARD)
    end = re.compile(r"^fi$", re.MULTILINE).search(_INSTALL_SH, guard)
    assert end is not None
    for note in (
        "Start with: cd",
        "Verify hooks with POST",
        "To run as a background service:",
        "systemctl daemon-reload",
        "launchctl bootstrap",
    ):
        found = [m.start() for m in re.finditer(re.escape(note), _INSTALL_SH)]
        assert found, note
        assert all(guard < at < end.start() for at in found), (
            f"{note!r} must be printed only outside owned mode"
        )


def test_owned_mode_says_who_restarts_the_gateway():
    bare = _INSTALL_SH.index("restart it your usual way to load the plugin bundle")
    owned = _INSTALL_SH.index('elif [ "$OWNED_OPENCLAW" -eq 1 ]; then', bare - 400)
    assert owned < bare, "the owned-mode branch must come before the bare-metal hint"
    assert "container entrypoint restarts the gateway" in _INSTALL_SH[owned:bare]
