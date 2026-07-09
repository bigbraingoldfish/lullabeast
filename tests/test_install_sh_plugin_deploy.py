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
    """The deployed extension copy must never be world-writable. A
    bind-mounted repo (Windows/macOS Docker) reports mode 777; the install
    copy preserves it, OpenClaw blocks world-writable plugin paths, and an
    owned-mode boot then crash-loops (observed live on the first Windows
    dev-container boot, 2026-07-09). Three layers guard it: install from a
    sane-permission staging copy, chmod the deployed tree post-install, and
    the entrypoint heals prior boots' trees before the gateway scans them."""
    # Layer 1: staged install (fixes the mode at the source).
    assert "PLUGIN_STAGE=$(mktemp -d)" in install_sh_code
    stage_chmod = install_sh_code.find('chmod -R go-w "$PLUGIN_STAGE"')
    stage_install = install_sh_code.find('plugins install --force "$PLUGIN_STAGE"')
    assert stage_chmod != -1 and stage_install != -1 and stage_chmod < stage_install, (
        "install.sh must chmod the staging copy BEFORE `plugins install` runs on it"
    )
    # Layer 2: post-install chmod on the deployed tree.
    assert 'chmod -R go-w "$OPENCLAW_ROOT/extensions/autodev-pipeline-signals"' in install_sh_code
    # Layer 3: the container entrypoint heals previously-deployed trees
    # before the bootstrap gateway start.
    entrypoint = _strip_shell_comments(
        (_REPO_ROOT / "deploy" / "entrypoint.sh").read_text()
    )
    heal = entrypoint.find('chmod -R go-w "$OPENCLAW_ROOT/extensions"')
    gateway_start = entrypoint.find('say "starting OpenClaw gateway (bootstrap)"')
    assert heal != -1 and gateway_start != -1 and heal < gateway_start, (
        "entrypoint.sh must normalize extension perms before the bootstrap gateway start"
    )


def test_plugin_validation_failure_prints_diagnostics_before_fatal_warn(install_sh_code):
    """Owned mode exits on the first warn, so the inspect/perms diagnostics
    must precede it or a validation failure dies mute (the original Windows
    crash-loop gave no usable output)."""
    diag = install_sh_code.find("Plugin validation diagnostics")
    fatal = install_sh_code.find('warn "Plugin validation failed')
    assert diag != -1 and fatal != -1 and diag < fatal, (
        "validation diagnostics must print before the owned-mode-fatal warn"
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
