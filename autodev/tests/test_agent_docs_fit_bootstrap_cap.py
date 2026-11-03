"""Every agent bootstrap file fits OpenClaw's per-file injection cap.

OpenClaw truncates each injected bootstrap file (AGENTS.md, TOOLS.md, ...) at the
agent's ``bootstrapMaxChars``, which Lullabeast seeds to
``AUTODEV_BOOTSTRAP_MAX_CHARS``. An AGENTS.md ends with its Red Lines, so a file
over the cap silently loses the section that matters most. Measured with LF line
endings, as the files ship.
"""

import glob
import os

import pytest

from autodev.installer.setup_helpers import AUTODEV_BOOTSTRAP_MAX_CHARS

AGENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents"
)
BOOTSTRAP_FILES = sorted(glob.glob(os.path.join(AGENTS_DIR, "*", "*.md")))


def test_bootstrap_files_found():
    assert any(p.endswith(os.path.join("reviewer", "AGENTS.md")) for p in BOOTSTRAP_FILES)


@pytest.mark.parametrize(
    "path", BOOTSTRAP_FILES, ids=lambda p: os.path.relpath(p, AGENTS_DIR)
)
def test_bootstrap_file_fits_the_cap(path):
    with open(path, encoding="utf-8") as f:
        size = len(f.read().replace("\r\n", "\n"))
    assert size <= AUTODEV_BOOTSTRAP_MAX_CHARS, (
        f"{os.path.relpath(path, AGENTS_DIR)} is {size} chars; OpenClaw would cut "
        f"everything after {AUTODEV_BOOTSTRAP_MAX_CHARS}"
    )
