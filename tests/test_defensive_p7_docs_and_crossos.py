"""Defensive-hardening Phase 7 — static lint: docs match code + cross-OS hygiene.

These are repo-static greps (no app import), matching the idiom of
``test_docs_no_false_linux_only.py`` and ``test_gate_contract_doc_consistency.py``.
They cover:

  - T7.1 — CLAUDE.md no longer carries the stale ``_spawn_orchestrator``
    "known unfixed issue" claim nor the obsolete ``~/.openclaw`` fallback note.
  - T7.2 — the three fcntl-importing entry points guard the import with a
    friendly POSIX/WSL2 message (no raw ``ModuleNotFoundError`` on native
    Windows), the guard phrasing does not regress to a false "fcntl ...
    Linux-only" claim, and the server's project-name derivations use ``os.sep``.
  - T7.3 — every ``_mutate_queue`` / ``_mutate_queue_file`` CALL site carries a
    ``CAS-pure`` marker comment (the closure-purity footgun guard).
  - T7.4 — CLAUDE.md Security Constraints documents the reviewer gate's
    realpath/commonpath boundary check across behavioral, regression, and
    visual evidence.
"""
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"

# The three POSIX-only (fcntl) entry points.
_FCNTL_FILES = [
    _REPO_ROOT / "autodev" / "pipeline" / "orchestrator.py",
    _REPO_ROOT / "autodev" / "pipeline" / "heartbeat_cron.py",
    _REPO_ROOT / "ui" / "server.py",
]

# Reuse the existing doc-lint's "false Linux-only" pattern verbatim so the new
# guard strings are held to the same standard (fcntl is POSIX, not Linux-only).
_BAD_FCNTL_LINUX_ONLY = re.compile(r"fcntl[^.\n]*Linux[- ]only", re.IGNORECASE)

# try: \n import fcntl ... except ModuleNotFoundError ... WSL2  (in order).
_FCNTL_GUARD = re.compile(
    r"try:\s*\n\s*import fcntl\b.*?except ModuleNotFoundError.*?WSL2",
    re.DOTALL,
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── T7.1 — spawn-path doc reconciliation ─────────────────────────────────────

def test_claude_md_drops_spawn_path_known_unfixed_claim():
    text = _read(_CLAUDE_MD)
    assert "known unfixed issue" not in text, (
        "CLAUDE.md still calls the _spawn_orchestrator path 'a known unfixed "
        "issue' — it was resolved (server.py:1666-1667). Remove the stale claim."
    )


def test_claude_md_drops_stale_openclaw_repo_path_fallback():
    text = _read(_CLAUDE_MD)
    assert "which is wrong after migration but preserved for backward compatibility" not in text, (
        "CLAUDE.md still says autodev_repo_path falls back to ~/.openclaw; the "
        "real fallback is _AUTODEV_UI_ROOT (repo root) per server.py:471."
    )


def test_claude_md_shows_correct_orchestrator_construction():
    text = _read(_CLAUDE_MD)
    assert 'os.path.join(autodev_repo_path, "autodev", "pipeline", ORCHESTRATOR_FILENAME)' in text, (
        "CLAUDE.md should show the correct orchestrator-script construction."
    )


# ── T7.2 — POSIX/WSL2 fcntl guard + phrasing + os.sep ────────────────────────

@pytest.mark.parametrize("path", _FCNTL_FILES, ids=lambda p: p.name)
def test_fcntl_import_is_posix_guarded(path: Path):
    text = _read(path)
    assert _FCNTL_GUARD.search(text), (
        f"{path.name}: `import fcntl` must be wrapped in "
        f"`try/except ModuleNotFoundError` raising a friendly WSL2 message, so "
        f"native Windows fails cleanly instead of a raw ModuleNotFoundError."
    )


@pytest.mark.parametrize("path", _FCNTL_FILES, ids=lambda p: p.name)
def test_posix_guard_message_not_false_linux_only(path: Path):
    match = _BAD_FCNTL_LINUX_ONLY.search(_read(path))
    assert match is None, (
        f"{path.name} contains a false 'fcntl ... Linux-only' claim "
        f"(matched: {match.group(0)!r}); fcntl is POSIX and works on macOS."
    )


def test_server_project_name_derivation_uses_os_sep():
    """The `os.path.basename(<project>.rstrip(...))` project-name derivations
    must strip with `os.sep`, not a hardcoded '/'. RED until the 3 sites swap."""
    offenders = [
        f"server.py:{i}"
        for i, line in enumerate(_read(_REPO_ROOT / "ui" / "server.py").splitlines(), 1)
        if "basename(" in line and '.rstrip("/")' in line
    ]
    assert not offenders, (
        f"project-name derivations still use .rstrip('/') instead of "
        f".rstrip(os.sep): {offenders}"
    )


# ── T7.3 — CAS-pure marker at every call site ────────────────────────────────

def _call_sites_missing_marker(path: Path, call_token: str) -> list:
    """Return 1-based line numbers of `call_token` CALL sites (excluding the
    `def`) that lack a `CAS-pure` marker comment within the preceding 3 lines."""
    lines = _read(path).splitlines()
    missing = []
    for i, line in enumerate(lines):
        if call_token not in line or line.lstrip().startswith("def "):
            continue
        window = lines[max(0, i - 3):i]
        if not any("CAS-pure" in w for w in window):
            missing.append(i + 1)
    return missing


def test_orchestrator_cas_call_sites_carry_purity_marker():
    missing = _call_sites_missing_marker(
        _REPO_ROOT / "autodev" / "pipeline" / "orchestrator.py", "self._mutate_queue(")
    assert not missing, (
        f"orchestrator.py _mutate_queue call sites without a CAS-pure marker "
        f"comment (closure must stay side-effect-free): lines {missing}"
    )


def test_server_cas_call_sites_carry_purity_marker():
    missing = _call_sites_missing_marker(
        _REPO_ROOT / "ui" / "server.py", "_mutate_queue_file(config")
    assert not missing, (
        f"server.py _mutate_queue_file call sites without a CAS-pure marker "
        f"comment (closure must stay side-effect-free): lines {missing}"
    )


# ── T7.4 — reviewer-gate boundary documented ─────────────────────────────────

def test_claude_md_reviewer_gate_boundary_documented():
    text = _read(_CLAUDE_MD).lower()
    assert "behavioral, regression, and visual" in text, (
        "CLAUDE.md Security Constraints should document that the reviewer gate "
        "applies the realpath/commonpath workspace-boundary check uniformly to "
        "behavioral, regression, and visual evidence (T7.4)."
    )
