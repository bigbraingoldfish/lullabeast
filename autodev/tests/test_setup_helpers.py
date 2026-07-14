"""Installer helpers: exec-approvals refresh, .env merge."""

import json
from pathlib import Path

from autodev.installer import setup_helpers


def test_refresh_exec_approvals_rewrites_stale_gate_path(tmp_path):
    repo = tmp_path / "repo"
    gate_dir = repo / "autodev" / "pipeline" / "gate_scripts"
    gate_dir.mkdir(parents=True)
    g = gate_dir / "planner_gate.py"
    g.write_text("# gate")
    approvals = tmp_path / "exec-approvals.json"
    old = "/nope/gate_scripts/planner_gate.py"
    approvals.write_text(
        json.dumps({"version": 1, "agents": {"x": {old: {"approved": True}}}})
    )
    assert setup_helpers.refresh_exec_approvals_gate_paths(str(approvals), str(repo)) == "updated"
    data = json.loads(approvals.read_text())
    assert old not in json.dumps(data)
    assert str(g) in json.dumps(data)


def test_set_openclaw_global_tools_profile_updates(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"version": 1, "tools": {"profile": "minimal"}}))
    assert setup_helpers.set_openclaw_global_tools_profile(str(oc), "coding") == "updated"
    data = json.loads(oc.read_text())
    assert data["tools"]["profile"] == "coding"


def test_set_openclaw_global_tools_profile_unchanged(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"tools": {"profile": "coding"}}))
    assert setup_helpers.set_openclaw_global_tools_profile(str(oc), "coding") == "unchanged"


def test_patch_openclaw_hooks_creates_hooks_with_token(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"version": "1"}))
    r = setup_helpers.patch_openclaw_hooks_baseline(
        str(oc), token_if_missing="pipeline-secret-test"
    )
    assert r == "updated"
    data = json.loads(oc.read_text())
    h = data["hooks"]
    assert h["enabled"] is True
    assert h["token"] == "pipeline-secret-test"
    assert h["allowRequestSessionKey"] is True
    assert "pipeline:" in h["allowedSessionKeyPrefixes"]
    assert "ideas:" in h["allowedSessionKeyPrefixes"]


def test_patch_openclaw_hooks_preserves_existing_token(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(
        json.dumps(
            {
                "hooks": {
                    "enabled": True,
                    "token": "user-secret",
                    "allowRequestSessionKey": True,
                    "allowedSessionKeyPrefixes": ["pipeline:"],
                }
            }
        )
    )
    r = setup_helpers.patch_openclaw_hooks_baseline(
        str(oc), token_if_missing="would-not-use"
    )
    assert r in ("updated", "unchanged")
    data = json.loads(oc.read_text())
    assert data["hooks"]["token"] == "user-secret"
    assert "ideas:" in data["hooks"]["allowedSessionKeyPrefixes"]


def test_patch_openclaw_hooks_merges_prefixes_without_clobber(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(
        json.dumps(
            {
                "hooks": {
                    "token": "t",
                    "allowedSessionKeyPrefixes": ["custom:", "pipeline:"],
                }
            }
        )
    )
    assert setup_helpers.patch_openclaw_hooks_baseline(str(oc)) == "updated"
    prefs = json.loads(oc.read_text())["hooks"]["allowedSessionKeyPrefixes"]
    assert prefs[0] == "custom:"
    assert "pipeline:" in prefs
    assert "ideas:" in prefs


def test_patch_openclaw_hooks_without_token_skips_token_but_fixes_flags(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"hooks": {}}))
    r = setup_helpers.patch_openclaw_hooks_baseline(str(oc), token_if_missing=None)
    assert r == "updated"
    data = json.loads(oc.read_text())
    assert "token" not in data["hooks"] or data["hooks"].get("token") in (None, "")
    assert data["hooks"]["enabled"] is True
    assert data["hooks"]["allowRequestSessionKey"] is True


def test_patch_openclaw_hooks_missing_file(tmp_path):
    missing = tmp_path / "nope.json"
    assert setup_helpers.patch_openclaw_hooks_baseline(str(missing)).startswith("error:")


def test_openclaw_hooks_issues_detects_gaps(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"version": "1"}))
    iss = setup_helpers.openclaw_hooks_issues(str(oc))
    assert "no_hooks_object" in iss

    oc.write_text(
        json.dumps(
            {
                "hooks": {
                    "enabled": True,
                    "token": "x",
                    "allowRequestSessionKey": True,
                    "allowedSessionKeyPrefixes": ["pipeline:", "ideas:"],
                }
            }
        )
    )
    assert setup_helpers.openclaw_hooks_issues(str(oc)) == []


def test_merge_dotenv_missing_keys_appends(tmp_path):
    envp = tmp_path / ".env"
    envp.write_text("OPENCLAW_ROOT=/a\n")
    r = setup_helpers.merge_dotenv_missing_keys(
        str(envp),
        {"AUTODEV_REPO_PATH": "/r", "AUTODEV_PIPELINE_ROOT": "/r/.autodev"},
    )
    assert r == "updated"
    text = envp.read_text()
    assert "AUTODEV_REPO_PATH=/r" in text
    assert "AUTODEV_PIPELINE_ROOT=" in text


def test_merge_dotenv_emits_canonical_names_only(tmp_path):
    """Fresh installs must write canonical env vars only; legacy aliases must
    never be emitted."""
    envp = tmp_path / ".env"
    r = setup_helpers.merge_dotenv_missing_keys(
        str(envp),
        {
            "OPENCLAW_ROOT": "/oc",
            "AUTODEV_REPO_PATH": "/r",
            "AUTODEV_PIPELINE_ROOT": "/r/.autodev",
        },
    )
    assert r == "created"
    text = envp.read_text()
    assert "OPENCLAW_ROOT=/oc" in text
    assert "AUTODEV_PIPELINE_ROOT=/r/.autodev" in text
    assert "AUTODEV_ROOT=" not in text
    assert "AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME" not in text


def test_ensure_dotenv_stall_timeout_hints_appends_then_idempotent(tmp_path):
    envp = tmp_path / ".env"
    envp.write_text("OPENCLAW_ROOT=/a\n")
    assert setup_helpers.ensure_dotenv_stall_timeout_hints(str(envp)) == "appended"
    text = envp.read_text()
    assert setup_helpers.DOTENV_STALL_HINT_MARKER in text
    # Both knobs of the two-knob design must be present so fresh installs
    # discover both via the same comment block.
    assert "# AUTODEV_STALL_TIMEOUT_PLANNER=" in text
    assert "# AUTODEV_STALL_TIMEOUT_EXECUTOR=" in text
    assert "# AUTODEV_STALL_TIMEOUT_REVIEWER=" in text
    assert "# AUTODEV_STARTUP_GRACE_PLANNER=" in text
    assert "# AUTODEV_STARTUP_GRACE_EXECUTOR=" in text
    assert "# AUTODEV_STARTUP_GRACE_REVIEWER=" in text
    assert setup_helpers.ensure_dotenv_stall_timeout_hints(str(envp)) == "unchanged"


def test_ensure_dotenv_stall_timeout_hints_missing_file(tmp_path):
    missing = tmp_path / "missing.env"
    assert setup_helpers.ensure_dotenv_stall_timeout_hints(str(missing)) == "unchanged"


def test_ensure_dotenv_ideas_idle_hints_appends_then_idempotent(tmp_path):
    envp = tmp_path / ".env"
    envp.write_text("OPENCLAW_ROOT=/a\n")
    assert setup_helpers.ensure_dotenv_ideas_idle_hints(str(envp)) == "appended"
    text = envp.read_text()
    assert setup_helpers.DOTENV_IDEAS_IDLE_HINT_MARKER in text
    assert "# AUTODEV_IDEAS_IDLE_THRESHOLD=" in text
    assert setup_helpers.ensure_dotenv_ideas_idle_hints(str(envp)) == "unchanged"


def test_ensure_dotenv_ideas_history_budget_hint_appends_then_idempotent(tmp_path):
    envp = tmp_path / ".env"
    envp.write_text("OPENCLAW_ROOT=/a\n")
    assert setup_helpers.ensure_dotenv_ideas_history_budget_hint(str(envp)) == "appended"
    text = envp.read_text()
    assert setup_helpers.DOTENV_IDEAS_HISTORY_BUDGET_HINT_MARKER in text
    assert "# AUTODEV_IDEAS_HISTORY_CHAR_BUDGET=" in text
    # Documented default must match the value used by the server (20000).
    assert "20000" in text
    assert setup_helpers.ensure_dotenv_ideas_history_budget_hint(str(envp)) == "unchanged"


def test_ensure_dotenv_ideas_history_budget_hint_missing_file(tmp_path):
    missing = tmp_path / "missing.env"
    assert setup_helpers.ensure_dotenv_ideas_history_budget_hint(str(missing)) == "unchanged"


def test_ensure_dotenv_ideas_history_budget_hint_independent_of_idle_marker(tmp_path):
    """An existing idle-hints block must not block the history-budget append (different marker)."""
    envp = tmp_path / ".env"
    envp.write_text("OPENCLAW_ROOT=/a\n")
    assert setup_helpers.ensure_dotenv_ideas_idle_hints(str(envp)) == "appended"
    assert setup_helpers.ensure_dotenv_ideas_history_budget_hint(str(envp)) == "appended"
    text = envp.read_text()
    assert setup_helpers.DOTENV_IDEAS_IDLE_HINT_MARKER in text
    assert setup_helpers.DOTENV_IDEAS_HISTORY_BUDGET_HINT_MARKER in text


def test_env_example_includes_every_helper_placeholder():
    """Drift guard: every AUTODEV_* commented placeholder the helpers append must also
    exist (commented) in the committed .env.example template.
    """
    import os
    import re
    import tempfile

    repo_root = Path(__file__).resolve().parents[2]
    example_path = repo_root / ".env.example"
    assert example_path.is_file(), f".env.example missing at {example_path}"
    example_text = example_path.read_text()

    # Render every helper block once into a scratch .env, then extract every
    # ``# AUTODEV_*=`` placeholder from the combined output.
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / ".env"
        scratch.write_text("OPENCLAW_ROOT=/a\n")
        setup_helpers.ensure_dotenv_stall_timeout_hints(str(scratch))
        setup_helpers.ensure_dotenv_ideas_idle_hints(str(scratch))
        setup_helpers.ensure_dotenv_ideas_history_budget_hint(str(scratch))
        rendered = scratch.read_text()

    placeholders = set(re.findall(r"^#\s*(AUTODEV_[A-Z0-9_]+)=", rendered, re.M))
    assert placeholders, "no AUTODEV_* placeholders extracted from helper output"

    missing = sorted(
        p for p in placeholders
        if not re.search(rf"^#\s*{re.escape(p)}=", example_text, re.M)
    )
    assert not missing, f".env.example missing placeholders that helpers append: {missing}"


def test_read_openclaw_hooks_token_returns_value(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"hooks": {"token": "abc123"}}))
    assert setup_helpers.read_openclaw_hooks_token(str(oc)) == "abc123"


def test_webhook_secret_sync_assess_detects_placeholder_mismatch(tmp_path):
    oc = tmp_path / "openclaw.json"
    ui_cfg = tmp_path / "config.json"
    envp = tmp_path / ".env"
    oc.write_text(json.dumps({"hooks": {"token": "real-token"}}))
    ui_cfg.write_text(json.dumps({"hooks_token": "pipeline-secret-token"}))
    envp.write_text("AUTODEV_HOOKS_TOKEN=real-token\n")

    result = setup_helpers.webhook_secret_sync_assess(str(oc), str(ui_cfg), str(envp))
    assert result.summary_code() == "mismatch_ui"
    assert result.ui_needs_sync is True
    assert result.env_wrong is False


def test_set_ui_config_hooks_token_updates_atomically(tmp_path):
    ui_cfg = tmp_path / "config.json"
    ui_cfg.write_text(json.dumps({"port": 18790, "hooks_token": "old"}))
    r = setup_helpers.set_ui_config_hooks_token(str(ui_cfg), "new-token")
    assert r == "updated"
    data = json.loads(ui_cfg.read_text())
    assert data["hooks_token"] == "new-token"
    assert data["port"] == 18790


def test_set_dotenv_key_replaces_existing_value(tmp_path):
    envp = tmp_path / ".env"
    envp.write_text("OPENCLAW_ROOT=/x\nAUTODEV_HOOKS_TOKEN=old\n")
    r = setup_helpers.set_dotenv_key(str(envp), "AUTODEV_HOOKS_TOKEN", "new")
    assert r == "updated"
    text = envp.read_text()
    assert "AUTODEV_HOOKS_TOKEN=new" in text
    assert "AUTODEV_HOOKS_TOKEN=old" not in text


def test_force_dotenv_keys_overwrites_and_preserves_other_lines(tmp_path):
    # The container entrypoint's seeding path: a dev bind mount carries a
    # bare-metal .env whose path/token keys must be overwritten while tuning
    # knobs and comments survive verbatim.
    envp = tmp_path / ".env"
    envp.write_text(
        "# host file\n"
        "OPENCLAW_ROOT=~/.openclaw\n"
        "AUTODEV_STALL_TIMEOUT_EXECUTOR=1800\n"
        "AUTODEV_UI_TOKEN=stale\n"
    )
    r = setup_helpers.force_dotenv_keys(
        str(envp),
        {"OPENCLAW_ROOT": "/data/openclaw", "AUTODEV_UI_TOKEN": "fresh", "NEW_KEY": "v"},
    )
    assert r == "updated"
    text = envp.read_text()
    assert "OPENCLAW_ROOT=/data/openclaw" in text
    assert "~/.openclaw" not in text
    assert "AUTODEV_UI_TOKEN=fresh" in text
    assert "stale" not in text
    assert "AUTODEV_STALL_TIMEOUT_EXECUTOR=1800" in text
    assert "# host file" in text
    assert "NEW_KEY=v" in text


def test_force_dotenv_keys_creates_missing_file_and_is_idempotent(tmp_path):
    envp = tmp_path / ".env"
    pairs = {"A": "1", "B": "2"}
    assert setup_helpers.force_dotenv_keys(str(envp), pairs) == "created"
    assert setup_helpers.force_dotenv_keys(str(envp), pairs) == "unchanged"
    text = envp.read_text()
    assert text.count("A=1") == 1 and text.count("B=2") == 1


def test_force_dotenv_keys_dedupes_repeated_key(tmp_path):
    envp = tmp_path / ".env"
    envp.write_text("K=one\nK=two\n")
    assert setup_helpers.force_dotenv_keys(str(envp), {"K": "three"}) == "updated"
    text = envp.read_text()
    assert text.count("K=") == 1
    assert "K=three" in text


# ---------------------------------------------------------------------------
# Context-limit truncation seeding (bootstrapMaxChars / postCompaction*).
#
# Audit: plans/Active/metaprompt-2-truncation-settings-audit.md.
# Regime 1 (bootstrap files): OpenClaw truncates each injected AGENTS.md at the
# 12k per-file default, cutting off the Stage A ``## Always-Apply: ...`` rules
# (which begin past byte ~10k in every pipeline role's AGENTS.md). Compaction
# compounds it: the post-compaction refresh re-injects only the sections named
# in ``agents.defaults.compaction.postCompactionSections`` (OpenClaw default
# ["Session Startup","Red Lines"] — names our AGENTS.md does NOT contain),
# capped at per-agent ``contextLimits.postCompactionMaxChars`` (default 1800).
# Net effect today: the universal rules are dropped on every compaction.
#
# These tests pin the AutoDev fix: raise the bootstrap cap on all six agents,
# point the post-compaction refresh at our real section names, and size its cap
# to hold them. They fail until ``ensure_openclaw_context_limits`` and the
# canonical constants exist.
# ---------------------------------------------------------------------------

_AUTODEV_SIX = ("planner", "executor", "reviewer", "escalation", "prd-creator", "roadmap-converter")


def _oc_with_six_agents(tmp_path):
    oc = tmp_path / "openclaw.json"
    agents = [{"id": a, "workspace": f"/ws/{a}", "model": {"primary": "m"}} for a in _AUTODEV_SIX]
    oc.write_text(json.dumps({"agents": {"list": agents}, "hooks": {}}))
    return oc


def test_context_limit_constants_present():
    """Canonical truncation values live in setup_helpers (single source of truth)."""
    assert setup_helpers.AUTODEV_BOOTSTRAP_MAX_CHARS == 32000
    assert setup_helpers.AUTODEV_POSTCOMPACTION_MAX_CHARS == 8000
    assert tuple(setup_helpers.AUTODEV_BOOTSTRAP_AGENT_IDS) == _AUTODEV_SIX
    assert tuple(setup_helpers.AUTODEV_POSTCOMPACTION_AGENT_IDS) == ("planner", "executor", "reviewer")
    secs = list(setup_helpers.AUTODEV_POSTCOMPACTION_SECTIONS)
    assert "Always-Apply: Integration Wiring" in secs
    assert "Always-Apply: Testing Quality" in secs


def test_ensure_context_limits_sets_bootstrap_on_all_six_agents(tmp_path):
    oc = _oc_with_six_agents(tmp_path)
    assert setup_helpers.ensure_openclaw_context_limits(str(oc)) == "updated"
    data = json.loads(oc.read_text())
    for e in data["agents"]["list"]:
        assert e["bootstrapMaxChars"] == 32000, e["id"]


def test_ensure_context_limits_postcompaction_pipeline_only(tmp_path):
    oc = _oc_with_six_agents(tmp_path)
    setup_helpers.ensure_openclaw_context_limits(str(oc))
    data = json.loads(oc.read_text())
    by_id = {e["id"]: e for e in data["agents"]["list"]}
    for a in ("planner", "executor", "reviewer"):
        assert by_id[a]["contextLimits"]["postCompactionMaxChars"] == 8000, a
    # Non-pipeline agents have no Always-Apply sections, so no post-compaction cap.
    for a in ("escalation", "prd-creator", "roadmap-converter"):
        assert "postCompactionMaxChars" not in by_id[a].get("contextLimits", {}), a


def test_ensure_context_limits_seeds_postcompaction_sections_default(tmp_path):
    oc = _oc_with_six_agents(tmp_path)
    setup_helpers.ensure_openclaw_context_limits(str(oc))
    data = json.loads(oc.read_text())
    secs = data["agents"]["defaults"]["compaction"]["postCompactionSections"]
    assert "Always-Apply: Integration Wiring" in secs
    assert "Always-Apply: Testing Quality" in secs


def test_ensure_context_limits_merges_and_dedupes_existing_sections(tmp_path):
    oc = tmp_path / "openclaw.json"
    agents = [{"id": a, "workspace": f"/ws/{a}"} for a in _AUTODEV_SIX]
    oc.write_text(
        json.dumps(
            {
                "agents": {
                    "list": agents,
                    "defaults": {
                        "compaction": {"postCompactionSections": ["Red Lines", "Custom Section"]}
                    },
                }
            }
        )
    )
    setup_helpers.ensure_openclaw_context_limits(str(oc))
    data = json.loads(oc.read_text())
    secs = data["agents"]["defaults"]["compaction"]["postCompactionSections"]
    assert secs.count("Red Lines") == 1  # preserved, not duplicated
    assert "Custom Section" in secs  # operator additions preserved
    assert "Always-Apply: Integration Wiring" in secs  # ours appended


def test_ensure_context_limits_idempotent(tmp_path):
    oc = _oc_with_six_agents(tmp_path)
    assert setup_helpers.ensure_openclaw_context_limits(str(oc)) == "updated"
    assert setup_helpers.ensure_openclaw_context_limits(str(oc)) == "unchanged"


def test_ensure_context_limits_preserves_unrelated_keys(tmp_path):
    oc = tmp_path / "openclaw.json"
    agents = [
        {
            "id": "executor",
            "workspace": "/ws/executor",
            "model": {"primary": "m"},
            "tools": {"allow": ["read"]},
        }
    ]
    oc.write_text(json.dumps({"agents": {"list": agents}}))
    setup_helpers.ensure_openclaw_context_limits(str(oc))
    e = json.loads(oc.read_text())["agents"]["list"][0]
    assert e["model"]["primary"] == "m"
    assert e["tools"]["allow"] == ["read"]
    assert e["bootstrapMaxChars"] == 32000


def test_ensure_context_limits_ignores_non_autodev_agents(tmp_path):
    oc = tmp_path / "openclaw.json"
    agents = [
        {"id": "executor", "workspace": "/ws/executor"},
        {"id": "some-other-agent", "workspace": "/ws/other"},
    ]
    oc.write_text(json.dumps({"agents": {"list": agents}}))
    setup_helpers.ensure_openclaw_context_limits(str(oc))
    by_id = {e["id"]: e for e in json.loads(oc.read_text())["agents"]["list"]}
    assert by_id["executor"]["bootstrapMaxChars"] == 32000
    assert "bootstrapMaxChars" not in by_id["some-other-agent"]


def test_ensure_context_limits_missing_file(tmp_path):
    assert setup_helpers.ensure_openclaw_context_limits(str(tmp_path / "nope.json")).startswith("error:")


# The seeded sections every pipeline AGENTS.md must carry as a literal ``## <name>``
# header. "Session Startup" is deliberately absent — it is an OpenClaw default kept in
# the seed for future agents, not a header our AGENTS.md files carry.
_REQUIRED_POSTCOMPACTION_HEADERS = (
    "Always-Apply: Integration Wiring",
    "Always-Apply: Testing Quality",
    "Always-Apply: Orchestrator Control",
    "Red Lines",
)


def test_postcompaction_sections_match_real_agents_md_headers():
    """Drift guard: every seeded section our AGENTS.md files rely on for the
    post-compaction refresh (the three ``Always-Apply: *`` rules plus ``Red Lines``,
    the compact output-contract restatement) must exist as a literal ``## <name>``
    header in all three pipeline AGENTS.md files. If a header is renamed without
    updating the seed, the refresh silently stops re-injecting it — the exact
    failure this audit fixes.
    """
    repo_root = Path(__file__).resolve().parents[2]
    for name in _REQUIRED_POSTCOMPACTION_HEADERS:
        assert name in setup_helpers.AUTODEV_POSTCOMPACTION_SECTIONS, (
            f"'{name}' missing from AUTODEV_POSTCOMPACTION_SECTIONS seed"
        )
    for role in setup_helpers.AUTODEV_POSTCOMPACTION_AGENT_IDS:
        md = (repo_root / "autodev" / "agents" / role / "AGENTS.md").read_text()
        for name in _REQUIRED_POSTCOMPACTION_HEADERS:
            assert f"## {name}" in md, (
                f"{role}/AGENTS.md missing '## {name}' — postCompactionSections drift; "
                f"keep the seed and the AGENTS.md header in sync."
            )


def test_postcompaction_cap_covers_largest_always_apply_block():
    """Drift guard: postCompactionMaxChars must be >= the COMBINED size of ALL
    seeded sections present in every pipeline AGENTS.md (the post-compaction refresh
    re-injects every one it finds), or the cap would truncate the very rules it exists
    to preserve. Four sections now (Integration Wiring + Testing Quality + Orchestrator
    Control + Red Lines); cap is 8000.
    """
    import re as _re

    repo_root = Path(__file__).resolve().parents[2]
    for role in setup_helpers.AUTODEV_POSTCOMPACTION_AGENT_IDS:
        md = (repo_root / "autodev" / "agents" / role / "AGENTS.md").read_text()
        total = 0
        for name in setup_helpers.AUTODEV_POSTCOMPACTION_SECTIONS:
            header = f"## {name}"
            start = md.find(header)
            if start == -1:
                continue  # e.g. "Session Startup" — seeded for OpenClaw defaults, not present here
            m = _re.search(r"\n## ", md[start + len(header):])
            end = start + len(header) + m.start() if m else len(md)
            total += end - start
        assert total <= setup_helpers.AUTODEV_POSTCOMPACTION_MAX_CHARS, (
            f"{role}/AGENTS.md seeded post-compaction sections total {total} chars but the "
            f"cap is {setup_helpers.AUTODEV_POSTCOMPACTION_MAX_CHARS}; raise "
            f"AUTODEV_POSTCOMPACTION_MAX_CHARS or trim the sections."
        )


# ---------------------------------------------------------------------------
# ensure_model_switch_allowlist — registered ⇒ switchable.
#
# The gateway only accepts a session-level model that appears in
# ``agents.defaults.models`` (a role's configured primary bypasses the list).
# Providers are registered by a different path, so without this sync a probed
# local model shows in every picker yet is rejected at session creation.
# ---------------------------------------------------------------------------


def _oc_with_providers(tmp_path, providers, defaults_models=None):
    oc = tmp_path / "openclaw.json"
    data = {
        "models": {"providers": providers},
        "agents": {"defaults": {"models": defaults_models or {}}, "list": []},
    }
    oc.write_text(json.dumps(data))
    return oc


def test_allowlist_seeds_every_registered_model(tmp_path):
    oc = _oc_with_providers(tmp_path, {
        "openrouter": {"models": [{"id": "qwen/qwen3.6-27b"}]},
        "local": {"models": [{"id": "qwen3.6-27b"}, {"id": "qwen3.6-35b-3a"}]},
    })
    assert setup_helpers.ensure_model_switch_allowlist(str(oc)) == "updated"
    data = json.loads(oc.read_text())
    models = data["agents"]["defaults"]["models"]
    assert models == {
        "openrouter/qwen/qwen3.6-27b": {},
        "local/qwen3.6-27b": {},
        "local/qwen3.6-35b-3a": {},
    }


def test_allowlist_never_touches_existing_entries(tmp_path):
    """Hand-tuned params (and operator-added entries for unregistered models)
    survive the sync byte-for-byte."""
    existing = {
        "openrouter/z-ai/glm-5.2": {"params": {"temperature": 0.6}},
        "openrouter/gone/removed-model": {"params": {"top_p": 0.9}},
    }
    oc = _oc_with_providers(
        tmp_path,
        {"openrouter": {"models": [{"id": "z-ai/glm-5.2"}, {"id": "new/model"}]}},
        defaults_models=existing,
    )
    assert setup_helpers.ensure_model_switch_allowlist(str(oc)) == "updated"
    models = json.loads(oc.read_text())["agents"]["defaults"]["models"]
    assert models["openrouter/z-ai/glm-5.2"] == {"params": {"temperature": 0.6}}
    assert models["openrouter/gone/removed-model"] == {"params": {"top_p": 0.9}}
    assert models["openrouter/new/model"] == {}


def test_allowlist_idempotent(tmp_path):
    oc = _oc_with_providers(tmp_path, {"local": {"models": [{"id": "m1"}]}})
    assert setup_helpers.ensure_model_switch_allowlist(str(oc)) == "updated"
    assert setup_helpers.ensure_model_switch_allowlist(str(oc)) == "unchanged"


def test_allowlist_creates_defaults_path_when_absent(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"models": {"providers": {"local": {"models": [{"id": "m1"}]}}}}))
    assert setup_helpers.ensure_model_switch_allowlist(str(oc)) == "updated"
    assert json.loads(oc.read_text())["agents"]["defaults"]["models"] == {"local/m1": {}}


def test_allowlist_skips_malformed_entries(tmp_path):
    oc = _oc_with_providers(tmp_path, {
        "local": {"models": [{"id": "good"}, {"name": "no-id"}, "not-a-dict", {"id": ""}]},
        "broken": "not-a-dict",
        "empty": {"models": []},
    })
    assert setup_helpers.ensure_model_switch_allowlist(str(oc)) == "updated"
    assert json.loads(oc.read_text())["agents"]["defaults"]["models"] == {"local/good": {}}


def test_allowlist_no_providers_is_unchanged(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"agents": {"defaults": {}}}))
    assert setup_helpers.ensure_model_switch_allowlist(str(oc)) == "unchanged"


def test_allowlist_missing_file_errors(tmp_path):
    assert setup_helpers.ensure_model_switch_allowlist(
        str(tmp_path / "missing.json")
    ) == "error:file not found"


def test_allowlist_malformed_defaults_models_errors(tmp_path):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({
        "models": {"providers": {"local": {"models": [{"id": "m1"}]}}},
        "agents": {"defaults": {"models": ["not", "a", "map"]}},
    }))
    result = setup_helpers.ensure_model_switch_allowlist(str(oc))
    assert result.startswith("error:")
