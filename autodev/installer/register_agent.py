#!/usr/bin/env python3
"""Register AutoDev pipeline agents in openclaw.json.

Ensures planner, executor, reviewer, escalation, prd-creator, and roadmap-converter
each have an ``agents.list`` entry and appear in ``hooks.allowedAgentIds``. Called by
install.sh (step 9). Importable for testing.

CLI usage:
    python register_agent.py <openclaw_json_path> <autodev_root> [--dry-run|--apply]

Return codes (printed to stdout, last line):
    already_registered  — all six agents present in list and hooks allowlist
    registered          — openclaw.json updated (--apply only)
    dry_run             — dry-run complete (--dry-run only)
    error:<message>     — unexpected failure

Warnings go to stderr so install.sh can use stdout | tail -1 for status.

Model selection (shared for new coding agents; escalation uses the same default but
gets PIPELINE-SPEC tool policy: read/write only):
    1) Copy from prd-creator entry in agents.list if present with model.
    2) Else if OpenRouter appears configured, use openrouter/minimax/minimax-m2.7.
    3) Else use agents.defaults.model if present, else MiniMax id with warning.

roadmap-converter copies per-agent ``tools`` from prd-creator when that entry defines
them; other coding agents omit ``tools`` so global ``tools.profile`` applies.
Escalation always gets an explicit restrictive ``tools`` block when newly added.

If ``agents.list`` is missing, it is created as []. Writes are atomic (mkstemp + os.replace).
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

MINIMAX_RECOMMENDED = "openrouter/minimax/minimax-m2.7"

# Canonical order for appending newly created entries (existing order preserved).
AUTODEV_AGENT_IDS = (
    "planner",
    "executor",
    "reviewer",
    "escalation",
    "prd-creator",
    "roadmap-converter",
)

ESCALATION_TOOLS = {
    "allow": ["read", "write"],
    "deny": ["edit", "apply_patch", "exec", "process", "browser"],
}

# Executor and reviewer need the browser tool for Playwright MCP — they capture
# (executor) and inspect (reviewer) screenshots on UI/INT phases. Without this,
# the reviewer gate's ERR_VISUAL_UNVERIFIED check rejects every UI phase.
EXECUTOR_TOOLS = {
    "allow": ["read", "write", "edit", "exec", "process", "browser"],
}
REVIEWER_TOOLS = {
    "allow": ["read", "write", "exec", "process", "browser"],
}

# Planner does not need browser access; it only writes plans. prd-creator is
# explicit about its tools elsewhere. Other coding agents inherit the global
# tools.profile via an absent `tools` key.
_CODING_WITHOUT_EXPLICIT_TOOLS = frozenset(
    {"planner", "prd-creator"}
)


def _eprint(msg: str, stream) -> None:
    if stream is not None:
        print(msg, file=stream)


def _normalize_agents(data: dict) -> str | None:
    """Ensure data['agents'] is a dict with a list agents.list. Return error token or None."""
    agents = data.get("agents")
    if agents is None:
        data["agents"] = {}
        agents = data["agents"]
    if not isinstance(agents, dict):
        return "error:unexpected openclaw.json structure — agents must be an object"
    lst = agents.get("list")
    if lst is None:
        agents["list"] = []
    elif not isinstance(lst, list):
        return "error:agents.list must be an array"
    return None


def _normalize_hooks(data: dict) -> None:
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        data["hooks"] = {}
        hooks = data["hooks"]
    ids = hooks.get("allowedAgentIds")
    if not isinstance(ids, list):
        hooks["allowedAgentIds"] = []


def _openrouter_configured(data: dict) -> bool:
    """True if OpenRouter provider or openrouter/* model entries appear configured."""
    models_root = data.get("models")
    if isinstance(models_root, dict):
        prov = models_root.get("providers")
        if isinstance(prov, dict) and isinstance(prov.get("openrouter"), dict):
            return True
    defaults = (data.get("agents") or {}).get("defaults") or {}
    mdl_map = defaults.get("models")
    if isinstance(mdl_map, dict):
        for key in mdl_map:
            if isinstance(key, str) and key.startswith("openrouter/"):
                return True
    return False


def _find_prd_creator(agents_list: list) -> dict | None:
    for entry in agents_list:
        if isinstance(entry, dict) and entry.get("id") == "prd-creator":
            return entry
    return None


def _resolve_shared_model(agents_list: list, data: dict, stderr) -> tuple[dict, list[str]]:
    """Return (model dict, stderr note lines) for newly registered pipeline agents."""
    notes: list[str] = []
    prd_creator = _find_prd_creator(agents_list)

    if prd_creator is not None and isinstance(prd_creator.get("model"), dict):
        model = copy.deepcopy(prd_creator["model"])
        notes.append("[register_agent] Using model copied from prd-creator for new agent entries.")
        for line in notes:
            _eprint(line, stderr)
        return model, notes

    if _openrouter_configured(data):
        notes.append(
            f"[register_agent] No prd-creator model to copy; OpenRouter appears configured. "
            f"Using recommended pipeline model: {MINIMAX_RECOMMENDED}"
        )
        for line in notes:
            _eprint(line, stderr)
        return {"primary": MINIMAX_RECOMMENDED, "fallbacks": []}, notes

    defaults = (data.get("agents") or {}).get("defaults") or {}
    dm = defaults.get("model")
    if isinstance(dm, dict) and dm.get("primary"):
        notes.append(
            "[register_agent] WARNING: No prd-creator entry and OpenRouter not detected in openclaw.json. "
            "Using agents.defaults.model.primary for new agents. "
            "For best results with this pipeline, configure OpenRouter and use "
            f"{MINIMAX_RECOMMENDED} (low-cost, instruction-following)."
        )
        for line in notes:
            _eprint(line, stderr)
        return copy.deepcopy(dm), notes

    notes.append(
        f"[register_agent] WARNING: No prd-creator, no OpenRouter block, no agents.defaults.model. "
        f"Applying default primary {MINIMAX_RECOMMENDED} — ensure your gateway has a matching provider "
        "or add prd-creator / OpenRouter config. Strongly recommend MiniMax 2.7 for this pipeline."
    )
    for line in notes:
        _eprint(line, stderr)
    return {"primary": MINIMAX_RECOMMENDED, "fallbacks": []}, notes


def _agent_ids_present(agents_list: list) -> set[str]:
    out: set[str] = set()
    for entry in agents_list:
        if isinstance(entry, dict):
            aid = entry.get("id")
            if isinstance(aid, str):
                out.add(aid)
    return out


def _build_new_entry(
    agent_id: str,
    autodev_root: str,
    shared_model: dict,
    working_list: list,
    stderr,
) -> dict:
    """Build one agents.list entry for ``agent_id`` (not yet persisted)."""
    entry: dict = {
        "id": agent_id,
        "workspace": os.path.join(autodev_root, f"workspace-{agent_id}"),
        "model": copy.deepcopy(shared_model),
    }
    if agent_id == "escalation":
        entry["tools"] = copy.deepcopy(ESCALATION_TOOLS)
        primary = (shared_model.get("primary") or "") if isinstance(shared_model, dict) else ""
        if isinstance(primary, str) and primary and "llama" not in primary.lower():
            _eprint(
                "[register_agent] NOTE: escalation is often configured with a local llama model; "
                "verify model.primary if the human escalation loop misbehaves.",
                stderr,
            )
    elif agent_id == "executor":
        entry["tools"] = copy.deepcopy(EXECUTOR_TOOLS)
    elif agent_id == "reviewer":
        entry["tools"] = copy.deepcopy(REVIEWER_TOOLS)
    elif agent_id == "roadmap-converter":
        prd = _find_prd_creator(working_list)
        if prd is not None and isinstance(prd.get("tools"), dict):
            entry["tools"] = copy.deepcopy(prd["tools"])
    elif agent_id in _CODING_WITHOUT_EXPLICIT_TOOLS:
        entry.pop("tools", None)
    return entry


def register_autodev_agents(
    openclaw_json_path: str,
    autodev_root: str,
    dry_run: bool = False,
    stderr=None,
) -> str:
    """Ensure all AutoDev webhook agents exist in openclaw.json."""
    if stderr is None:
        stderr = sys.stderr

    try:
        with open(openclaw_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return f"error:file not found: {openclaw_json_path}"
    except json.JSONDecodeError as e:
        return f"error:invalid JSON in {openclaw_json_path}: {e}"
    except Exception as e:
        return f"error:{e}"

    if not isinstance(data, dict):
        return "error:openclaw.json root must be an object"

    err = _normalize_agents(data)
    if err:
        return err

    _normalize_hooks(data)

    agents_list = data["agents"]["list"]
    present = _agent_ids_present(agents_list)

    allowed_ids = data["hooks"]["allowedAgentIds"]
    hook_ids_set = {x for x in allowed_ids if isinstance(x, str)}

    need_agents = [aid for aid in AUTODEV_AGENT_IDS if aid not in present]
    need_hooks = [aid for aid in AUTODEV_AGENT_IDS if aid not in hook_ids_set]

    if not need_agents and not need_hooks:
        return "already_registered"

    shared_model, _ = _resolve_shared_model(agents_list, data, stderr)

    working = list(agents_list)
    new_entries: list[dict] = []
    for aid in AUTODEV_AGENT_IDS:
        if aid in present:
            continue
        entry = _build_new_entry(aid, autodev_root, shared_model, working, stderr)
        new_entries.append(entry)
        working.append(entry)
        present.add(aid)

    if dry_run:
        if new_entries:
            print("  Would add to agents.list in openclaw.json:")
            print(json.dumps(new_entries, indent=4))
        for aid in need_hooks:
            print(f"  Would add {aid!r} to hooks.allowedAgentIds")
        return "dry_run"

    if new_entries:
        data["agents"]["list"] = agents_list + new_entries

    ids = data["hooks"]["allowedAgentIds"]
    for aid in AUTODEV_AGENT_IDS:
        if aid not in hook_ids_set:
            ids.append(aid)
            hook_ids_set.add(aid)

    json_dir = os.path.dirname(os.path.abspath(openclaw_json_path))
    fd, tmp_path = tempfile.mkstemp(dir=json_dir, prefix="openclaw_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, openclaw_json_path)
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return f"error:write failed: {e}"

    return "registered"


def register_roadmap_converter(
    openclaw_json_path: str,
    autodev_root: str,
    dry_run: bool = False,
    stderr=None,
) -> str:
    """Backward-compatible alias for :func:`register_autodev_agents`."""
    return register_autodev_agents(openclaw_json_path, autodev_root, dry_run=dry_run, stderr=stderr)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Register AutoDev agents (planner … roadmap-converter) in openclaw.json"
    )
    parser.add_argument("openclaw_json", help="Path to openclaw.json")
    parser.add_argument("autodev_root", help="OpenClaw root directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not write")
    parser.add_argument("--apply", action="store_true", help="Perform the write")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        parser.error("Specify --dry-run or --apply")

    result = register_autodev_agents(
        args.openclaw_json,
        args.autodev_root,
        dry_run=args.dry_run,
        stderr=sys.stderr,
    )
    print(result)
    if result.startswith("error:"):
        sys.exit(1)
