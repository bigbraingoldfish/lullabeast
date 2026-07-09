"""Static lints for install.sh plugin-deploy correctness.

These guard three gaps found during the Ideas-chat hotfix validation, where a
fresh clone / re-run of install.sh could leave a STALE plugin bundle loaded in
the gateway even though the repo source was current:

1. ``openclaw plugins install`` without ``--force`` errors "plugin already
   exists" on any re-run (``git pull && ./install.sh``); the error was
   swallowed and the old bundle stayed deployed.
2. install.sh only *printed* "restart the gateway" — it never restarted it, so
   the freshly-installed bundle was never actually loaded.
3. ``openclaw plugins inspect`` confirms the typed hooks are *registered* but a
   stale bundle also has them registered — so "validated" could ship while the
   deployed code was old. A content-level grep of the deployed bundle catches
   that.

Each test fails red the moment a gap is reintroduced. They read install.sh as
text (no execution) — same approach as ``test_install_sh_portable.py``.
"""
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _REPO_ROOT / "install.sh"


def _strip_shell_comments(text: str) -> str:
    """Drop full-line shell comments so explanatory prose doesn't satisfy a
    lint that must be backed by real code. Shebang (``#!``) is preserved."""
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#") or line.startswith("#!")
    )


@pytest.fixture(scope="module")
def install_sh_code() -> str:
    return _strip_shell_comments(_INSTALL_SH.read_text())


# ── Gap 1: --force on plugin install (idempotent re-runs) ────────────────────

def test_plugin_install_uses_force(install_sh_code):
    """``openclaw plugins install`` must pass ``--force`` so a re-run replaces
    the existing extension instead of erroring 'already exists' and leaving the
    stale bundle in place."""
    assert "plugins install --force" in install_sh_code, (
        "install.sh must run `openclaw plugins install --force \"$PLUGIN_DIR\"` "
        "— without --force, re-runs silently keep the stale bundle deployed"
    )


def test_deployed_plugin_perms_normalized(install_sh_code):
    """The deployed extension copy must have group/other write stripped after
    install. A bind-mounted repo (Windows/macOS Docker) reports mode 777; the
    install copy preserves it, OpenClaw blocks world-writable plugin paths,
    and an owned-mode boot then crash-loops (observed live on the first
    Windows dev-container boot, 2026-07-09)."""
    assert 'chmod -R go-w "$OPENCLAW_ROOT/extensions/autodev-pipeline-signals"' in install_sh_code, (
        "install.sh must chmod -R go-w the deployed plugin after "
        "`plugins install` — bind-mounted repos deploy world-writable copies "
        "that OpenClaw blocks, crash-looping owned-mode boots"
    )


def test_no_bare_plugins_install_without_force(install_sh_code):
    """There must be no active ``plugins install \"$PLUGIN_DIR\"`` lacking
    --force (the bare form is the regression we are guarding)."""
    # Every active 'plugins install' occurrence that targets $PLUGIN_DIR must
    # carry --force. Flag a bare 'plugins install "$PLUGIN_DIR"'.
    assert 'plugins install "$PLUGIN_DIR"' not in install_sh_code, (
        "bare `plugins install \"$PLUGIN_DIR\"` (no --force) found — re-runs "
        "will error 'already exists' and skip the upgrade"
    )


# ── Gap 2: gateway restart so the new bundle actually loads ──────────────────

def test_install_restarts_gateway(install_sh_code):
    """install.sh must attempt to restart the OpenClaw gateway after installing
    the plugin — otherwise the freshly-built bundle is on disk but the running
    gateway keeps the old code in memory."""
    assert "systemctl --user restart openclaw-gateway" in install_sh_code, (
        "install.sh must restart the gateway (systemctl --user restart "
        "openclaw-gateway) so the new plugin bundle is loaded, not just printed"
    )


def test_gateway_restart_is_guarded(install_sh_code):
    """The restart must be guarded (systemd may be absent, e.g. bare macOS /
    non-systemd OpenClaw launches) so install.sh degrades gracefully instead of
    erroring on machines without the user service."""
    assert "systemctl --user is-active openclaw-gateway" in install_sh_code, (
        "gateway restart must be guarded by `systemctl --user is-active "
        "openclaw-gateway` so non-systemd hosts get a manual-restart hint "
        "instead of a hard error"
    )


# ── Gap 3: content-level check that the DEPLOYED bundle is current ───────────

def test_deployed_bundle_content_is_verified(install_sh_code):
    """install.sh must grep the *deployed* extension bundle for a marker only
    present in the current source, so a stale bundle can't pass as 'validated'
    on the strength of hook registration alone."""
    deployed_path = "extensions/autodev-pipeline-signals/dist/index.js"
    assert deployed_path in install_sh_code, (
        "install.sh must reference the deployed bundle "
        f"({deployed_path}) to verify its contents, not only run "
        "`openclaw plugins inspect`"
    )
    # A grep for a hotfix marker string — the Ideas production-form regex is a
    # stable, code-only marker introduced by the session-key hotfix.
    assert "agent:" in install_sh_code and "ideas:" in install_sh_code, (
        "install.sh bundle check must grep for a hotfix marker (e.g. the "
        "`agent:{role}:ideas:` matcher) to prove the deployed bundle is current"
    )
