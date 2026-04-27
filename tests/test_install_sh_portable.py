"""Static lints to keep install.sh portable across Linux and macOS.

Each assertion catches a known regression — a GNU-only flag, a bash 4+
feature, or a false "Linux only" claim — that would break macOS users.
Tests fail red the moment any of these is reintroduced.

See plans/let-s-build-a-formal-snappy-quail.md (Phase 2) for the matching
production-side fixes.
"""
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _REPO_ROOT / "install.sh"


def _strip_shell_comments(text: str) -> str:
    """Drop full-line shell comments so documentation referencing GNU-isms
    (e.g. "# `head -n -1` is GNU-only — use sed '$d'") doesn't trigger the
    lint. The shebang line is preserved (it begins with `#!` not `# `).

    Trailing inline comments are left in place — running code with an inline
    `# cp -u` comment is still considered a violation, since the lint can't
    cheaply distinguish "inert documentation" from "active code with a stray
    comment". Shell comments documenting the choice belong on their own line.
    """
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#") or line.startswith("#!")
    )


@pytest.fixture(scope="module")
def install_sh_text() -> str:
    return _INSTALL_SH.read_text()


@pytest.fixture(scope="module")
def install_sh_code(install_sh_text) -> str:
    """install.sh content with full-line comments stripped — used by the
    GNU-ism lints so explanatory comments don't false-positive."""
    return _strip_shell_comments(install_sh_text)


def test_install_sh_exists():
    assert _INSTALL_SH.is_file(), f"{_INSTALL_SH} missing"


def test_no_gnu_head_negative_count(install_sh_code):
    """`head -n -N` strips trailing N lines but is GNU-only; BSD head rejects
    negative counts. Use `sed '$d'` (BSD/GNU portable) instead."""
    assert "head -n -" not in install_sh_code, (
        "GNU-only `head -n -N` found in install.sh — replace with `sed '$d'`"
    )


def test_no_bash4_associative_arrays(install_sh_code):
    """`declare -A` requires bash 4+; macOS ships bash 3.2 by default. Use
    parallel indexed arrays of `key=value` pairs instead."""
    assert "declare -A" not in install_sh_code, (
        "Bash 4+ associative array (`declare -A`) found in install.sh — "
        "macOS ships bash 3.2; rewrite using indexed arrays"
    )


def test_no_gnu_cp_u(install_sh_code):
    """`cp -u` is a GNU coreutils flag absent from older macOS BSD cp. Use
    `[ ! -f $dst ] || [ $src -nt $dst ]; then cp ...` — the predicate the
    install script already uses in its dry-run preview block."""
    assert "cp -u" not in install_sh_code, (
        "`cp -u` (GNU coreutils flag) found in install.sh — replace with "
        "an explicit `-nt` test (matches the dry-run preview predicate)"
    )


def test_no_false_fcntl_linux_only_claim(install_sh_text):
    """install.sh must not claim AutoDev is Linux-only because of fcntl.
    fcntl is POSIX and works on macOS. The Windows-specific fail message is
    allowed because Windows native genuinely lacks fcntl."""
    bad_pattern = re.compile(r"fcntl[^.\n]*Linux[- ]only", re.IGNORECASE)
    match = bad_pattern.search(install_sh_text)
    assert match is None, (
        f"install.sh contains a false 'fcntl ... Linux-only' claim "
        f"(matched: {match.group(0)!r}); fcntl is POSIX and works on macOS"
    )
