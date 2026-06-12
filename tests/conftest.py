"""Shared fixtures for UI server tests.

Prevents the developer's real ``.env`` (sourced into the shell before running
pytest) from leaking into hermetic tests. Individual tests that set these via
``monkeypatch.setenv`` still work because the autouse fixture runs before the
per-test monkeypatch.
"""

import pytest


_ENV_KEYS_TO_SCRUB = (
    "OPENCLAW_ROOT",
    "AUTODEV_PIPELINE_ROOT",
    "AUTODEV_HOOKS_TOKEN",
    "AUTODEV_UI_TOKEN",
)


@pytest.fixture(autouse=True)
def _scrub_autodev_env(monkeypatch):
    for key in _ENV_KEYS_TO_SCRUB:
        monkeypatch.delenv(key, raising=False)
    # Hermeticity: the responseUsage pre-seed (`_preset_session_response_usage_sync`)
    # opens a real gateway WebSocket before each agent webhook. Tests fake the
    # hooks HTTP endpoint but not the WS control plane, so without this a test
    # run patches sessions into the developer's REAL gateway store and perturbs
    # the fake-server port timing. Dedicated tests opt back in via monkeypatch.
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "off")


@pytest.fixture(autouse=True)
def _neutralize_ui_token_auth(monkeypatch):
    """Complete ``_scrub_autodev_env``: also strip a *config-file*-sourced ``ui_token``.

    The dashboard auth middleware (``_TokenAuthMiddleware``) resolves the token per-request via
    ``_resolve_ui_token(load_config())``, which reads ``AUTODEV_UI_TOKEN`` **or** ``ui_token`` from
    the gitignored local ``ui/config.json``. ``_scrub_autodev_env`` clears only the env var, so on
    a developer machine with dashboard auth configured the ``ui/config.json`` token still leaks in
    and ``401``s every test that hits a non-exempt route without patching ``load_config`` — a
    hermeticity gap (the tests depend on the *absence* of a local config token), not a real bug.

    Wrap ``ui.server.load_config`` to blank ``ui_token`` — but ONLY when ``AUTODEV_UI_TOKEN`` is
    unset, mirroring ``load_config``'s own env-wins precedence. That preserves
    ``test_ui_auth_token.py::test_env_var_overrides_config``, which sets the env var and relies on
    the *real* ``load_config`` to enforce auth. Tests that ``patch("ui.server.load_config", ...)``
    themselves — the entire dedicated ``test_ui_auth_token.py`` suite — replace this wrapper during
    their ``with`` block, so real auth enforcement is unaffected and no test-file skip is needed.
    ``ui_token`` is consumed only by ``_resolve_ui_token`` (auth), so blanking it touches nothing else.
    """
    import os
    import ui.server as srv

    real = srv.load_config

    def _stripped(*args, **kwargs):
        cfg = real(*args, **kwargs)
        if isinstance(cfg, dict) and not os.environ.get("AUTODEV_UI_TOKEN", "").strip():
            cfg = {**cfg, "ui_token": ""}
        return cfg

    monkeypatch.setattr(srv, "load_config", _stripped)


@pytest.fixture(autouse=True)
def _disable_queue_autostart(request, monkeypatch):
    """Disable server-side queue auto-start by default across the whole UI suite.

    ``ui.server._maybe_autostart_queue`` starts the next eligible project by spawning a real
    orchestrator (via ``_queue_run_trigger_next_logic``). That orchestrator resolves the real
    ``OPENCLAW_ROOT`` / ``AUTODEV_PIPELINE_ROOT`` — the mock config omits them and the env-scrub
    above only forces the *fallback* chain, which still lands on the real ``~/.openclaw`` and the
    real ``<repo>/.autodev`` — so a stray auto-start from a test would mutate the live
    ``pipeline_state.json`` and repoint the live ``pipeline-project`` symlink.

    Any endpoint that makes a queue row READY (``add`` / ``parent``-clear / ``revalidate``) now
    calls this helper, so disable it by default. Tests that intentionally exercise auto-start opt
    in by setting ``_uses_real_autostart = True`` on their class and stub ``_spawn_orchestrator``
    themselves.
    """
    if getattr(getattr(request, "cls", None), "_uses_real_autostart", False):
        return
    monkeypatch.setattr(
        "ui.server._maybe_autostart_queue",
        lambda config: {"attempted": False, "reason": "test_disabled"},
        raising=False,
    )


@pytest.fixture(autouse=True)
def _protect_pipeline_symlinks():
    """Snapshot the live ``pipeline-project`` links and restore them after each test.

    A test that drives a symlink-writing endpoint (``_run_init_project`` / preflight / resume /
    switch) without fully isolating BOTH ``project_dir_path`` and ``openclaw_root`` can repoint
    the operator's real links — the documented "tests rewrite the shared symlink" hazard (see
    ``_disable_queue_autostart`` above), widened by the symmetric two-link write. ``_run_init_project``
    in particular reads the *real* ``load_config()`` internally, so even a test that patches
    ``ui.server.load_config`` does not isolate it. This belt-and-braces guard captures each link's
    target before the test and restores it afterward if it changed, so the suite can never strand
    a live run. No test asserts the real link persists across teardown (all use ``tmp_path`` links),
    so restoring only ever undoes accidental clobbering.
    """
    import os

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    links = [
        os.path.join(repo_root, ".autodev", "pipeline-project"),
        os.path.expanduser("~/.openclaw/pipeline-project"),
    ]
    snapshot = {}
    for link in links:
        try:
            if os.path.islink(link):
                snapshot[link] = os.readlink(link)
        except OSError:
            pass
    try:
        yield
    finally:
        for link, target in snapshot.items():
            try:
                if not os.path.islink(link) or os.readlink(link) != target:
                    tmp = f"{link}.restore_{os.getpid()}"
                    if os.path.lexists(tmp):
                        os.remove(tmp)
                    os.symlink(target, tmp)
                    os.replace(tmp, link)
            except OSError:
                pass


@pytest.fixture(scope="session", autouse=True)
def _prune_recent_projects_after_session():
    """Self-clean dead recents after the whole suite (4-B).

    Tests that drive real preflight / switch validation append the project's realpath to the
    operator's recents file (``~/.openclaw/ui_recent_projects.json``). When those paths are
    ``tmp_path`` dirs that are gone by session end, the entries become dead and would otherwise
    accumulate in the operator's UI. This session teardown runs AFTER every function-scoped
    ``_ui_recent_projects_path`` patch (e.g. ``recents_file``) is torn down, so it acts on the
    REAL file — and prune only removes entries whose directory no longer exists, never a live
    project. Best-effort: a failure here must never fail the suite.
    """
    yield
    try:
        from ui.server import post_setup_recent_projects_prune

        post_setup_recent_projects_prune()
    except Exception:
        pass
