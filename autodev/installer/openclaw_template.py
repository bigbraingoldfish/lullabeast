#!/usr/bin/env python3
"""Golden openclaw.json template helpers.

``deploy/openclaw.template.json`` is the one canonical, deliberately audited
OpenClaw config for owned-OpenClaw installs (the container). The full
key-by-key decision record for what it does and does not carry lives in
``deploy/CONFIG-AUDIT.md``. This module gives the template's three consumers
one shared implementation:

  * Rendering: ``render_template_text`` substitutes the ``${VAR}`` env
    placeholders (the container entrypoint renders the template into
    ``/data/openclaw/openclaw.json`` on first boot; tests render it against
    test values).
  * Conformance: ``template_conformance_issues`` diffs a live openclaw.json
    against the template's requirements. The doctor's ``template_conformance``
    check (owned mode only) uses it so config drift inside a container is
    loudly visible.
  * Reconciliation: ``reconcile_config_to_template`` is the write-side inverse
    of conformance. The container entrypoint reconciles the persisted config toward
    the current image's template on every boot, so a template change shipped in
    a new image self-heals instead of dead-ending at the (now-failing)
    conformance check.

Placeholder contract (whole-string ``${VAR}`` values only, so the raw template
file always parses as strict JSON):

  * ``HOOKS_TOKEN`` / ``GATEWAY_TOKEN`` are required at render time (the
    entrypoint generates them via ``secrets.token_urlsafe`` on first boot and
    persists them under ``/data``).
  * ``PLANNER_MODEL`` / ``EXECUTOR_MODEL`` / ``REVIEWER_MODEL`` / ``PRD_MODEL``
    / ``ROADMAP_MODEL`` / ``ESCALATION_MODEL`` default to the audit-picked
    models below when unset (one knob per agent role). The executor and
    reviewer defaults must stay multimodal (image input) because the reviewer
    gate demands visual verification on UI/INT phases.

Stdlib-only on purpose: the doctor imports this module, and the doctor must
run even when FastAPI/uvicorn are missing (python_deps is one of its checks).
"""

from __future__ import annotations

import copy
import json
import os
import re

# Template location, relative to the repo root (AUTODEV_REPO_PATH).
TEMPLATE_RELPATH = os.path.join("deploy", "openclaw.template.json")

# Whole-value placeholder shape: ``${UPPER_SNAKE}`` inside a JSON string.
TEMPLATE_PLACEHOLDER_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")

# Audit-picked model defaults (deploy/CONFIG-AUDIT.md). One knob
# per agent role; the executor and reviewer picks are multimodal by
# requirement. PLANNER_MODEL also backs agents.defaults.model. Every default
# must be one of the shipped, fully priced OpenRouter models in
# deploy/openclaw.template.json (tests enforce that every referenced model is
# shipped and priced; a shipped model may be a non-default, env-selectable
# option, e.g. minimax/minimax-m3 after the 2026-07-07 planner-default move).
TEMPLATE_MODEL_DEFAULTS: dict[str, str] = {
    "PLANNER_MODEL": "openrouter/moonshotai/kimi-k2.7-code",
    "EXECUTOR_MODEL": "openrouter/moonshotai/kimi-k2.7-code",
    "REVIEWER_MODEL": "openrouter/z-ai/glm-5.2",
    "PRD_MODEL": "openrouter/moonshotai/kimi-k2.7-code",
    "ROADMAP_MODEL": "openrouter/z-ai/glm-5.2",
    "ESCALATION_MODEL": "openrouter/qwen/qwen3.6-27b",
}

# Secrets with no sane default: rendering fails loud when these are unset.
TEMPLATE_REQUIRED_VARS: tuple[str, ...] = ("HOOKS_TOKEN", "GATEWAY_TOKEN")

# The full substitution contract. A placeholder outside this set is a template
# bug (tests/test_openclaw_template.py enforces both directions).
TEMPLATE_ENV_VARS: tuple[str, ...] = TEMPLATE_REQUIRED_VARS + tuple(
    TEMPLATE_MODEL_DEFAULTS
)


def template_path(repo_path: str) -> str:
    """Absolute path of the golden template inside the repo checkout."""
    return os.path.join(os.path.abspath(repo_path), TEMPLATE_RELPATH)


def template_placeholders(text: str) -> set[str]:
    """Return the set of ``${VAR}`` names appearing in the template text."""
    return set(TEMPLATE_PLACEHOLDER_RE.findall(text or ""))


def render_template_text(text: str, env: dict | None = None) -> str:
    """Substitute every ``${VAR}`` placeholder in ``text``.

    ``env`` maps var names to values (typically ``os.environ``). Model vars
    fall back to ``TEMPLATE_MODEL_DEFAULTS`` when unset or blank; a required
    var (``TEMPLATE_REQUIRED_VARS``) that resolves empty raises ``ValueError``
    naming the variable, so a mis-provisioned first boot fails loud instead of
    rendering an un-authenticatable config.
    """
    env = env or {}

    def _resolve(match: re.Match) -> str:
        name = match.group(1)
        raw = env.get(name)
        value = raw.strip() if isinstance(raw, str) else ""
        if not value:
            value = TEMPLATE_MODEL_DEFAULTS.get(name, "")
        if not value:
            raise ValueError(
                f"template variable {name} is required and has no value or default"
            )
        return value

    return TEMPLATE_PLACEHOLDER_RE.sub(_resolve, text)


def load_template(repo_path: str) -> dict:
    """Parse the raw (un-rendered) template. Raises on missing/invalid file."""
    with open(template_path(repo_path), "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("openclaw.template.json root must be a JSON object")
    return data


def _is_placeholder_string(value) -> bool:
    return isinstance(value, str) and bool(TEMPLATE_PLACEHOLDER_RE.search(value))


def _fmt(value) -> str:
    """Compact single-line rendering of a value for issue messages."""
    try:
        s = json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        s = repr(value)
    return s if len(s) <= 60 else s[:57] + "..."


def template_conformance_issues(template, live, path: str = "") -> list[str]:
    """Diff ``live`` config against the ``template``'s requirements.

    Direction matters: everything the template declares must be present in the
    live config with a matching value; extra live keys are tolerated (OpenClaw
    itself writes bookkeeping blocks like ``meta``/``wizard`` at runtime, and
    an operator may add providers). Rules per template node:

      * dict: every key must exist in live; recurse.
      * list of scalars: membership (each template item appears in the live
        list; extra live items tolerated, order ignored).
      * list of dicts: matched by ``id`` when both sides carry one (agents.list
        and provider model lists), recursing into the match; id-less dicts must
        have a deep-equal counterpart in the live list.
      * placeholder string (``${VAR}``): live value must be a non-empty string
        (the rendered value is deployment-specific, so only presence is
        checkable).
      * any other scalar: equality (bools never equal ints).

    Returns dotted-path issue strings; empty list means conformant.
    """
    issues: list[str] = []
    label = path or "(root)"

    if _is_placeholder_string(template):
        if not isinstance(live, str) or not live.strip():
            issues.append(f"{label}: expected a rendered non-empty string")
        return issues

    if isinstance(template, dict):
        if not isinstance(live, dict):
            issues.append(f"{label}: expected an object, found {_fmt(live)}")
            return issues
        for key, t_val in template.items():
            child = f"{path}.{key}" if path else str(key)
            if key not in live:
                issues.append(f"{child}: missing")
                continue
            issues.extend(template_conformance_issues(t_val, live[key], child))
        return issues

    if isinstance(template, list):
        if not isinstance(live, list):
            issues.append(f"{label}: expected an array, found {_fmt(live)}")
            return issues
        for idx, t_item in enumerate(template):
            if isinstance(t_item, dict) and isinstance(t_item.get("id"), str):
                t_id = t_item["id"]
                child = f"{path}[id={t_id}]"
                match = next(
                    (
                        e
                        for e in live
                        if isinstance(e, dict) and e.get("id") == t_id
                    ),
                    None,
                )
                if match is None:
                    issues.append(f"{child}: missing entry")
                    continue
                issues.extend(template_conformance_issues(t_item, match, child))
            elif isinstance(t_item, dict):
                if not any(
                    isinstance(e, dict)
                    and not template_conformance_issues(t_item, e, "")
                    for e in live
                ):
                    issues.append(f"{label}[{idx}]: no matching entry")
            else:
                if t_item not in live:
                    issues.append(f"{label}: missing item {_fmt(t_item)}")
        return issues

    # Scalar leaf. Bools first: isinstance(True, int) is True, so a plain
    # equality check would call a live 1 conformant with a template true.
    if isinstance(template, bool) or isinstance(live, bool):
        if not (isinstance(template, bool) and isinstance(live, bool) and template == live):
            issues.append(f"{label}: expected {_fmt(template)}, found {_fmt(live)}")
        return issues
    if template != live:
        issues.append(f"{label}: expected {_fmt(template)}, found {_fmt(live)}")
    return issues


def reconcile_config_to_template(template, live):
    """Return ``live`` with every requirement the ``template`` declares enforced.

    The write-side inverse of :func:`template_conformance_issues`: the result is
    guaranteed conformant (``template_conformance_issues(template, result)`` is
    empty for the same template) while keys and entries the template does *not*
    declare survive untouched. The container entrypoint uses this to heal a persisted
    ``openclaw.json`` toward a new image's template on boot, instead of leaving
    the drift to dead-end at install.sh's owned-mode validation or the doctor's
    ``template_conformance`` check.

    Pass a **rendered** template (``${VAR}`` placeholders already substituted) so
    no placeholder literal is ever written into the live config; the matching
    rules mirror the conformance ones exactly:

      * dict: every template key is enforced (recurse); live-only keys preserved.
      * list of dicts with a string ``id``: matched by ``id`` (recurse into the
        match, append a template entry that has no live counterpart); extra live
        entries preserved.
      * list of id-less dicts: a template entry with no deep-conformant live
        counterpart is appended.
      * list of scalars: template members ensured present (appended if missing);
        order and extra live items preserved.
      * scalar leaf: the template value wins.

    ``live`` is never mutated; a fresh structure is returned.
    """
    if isinstance(template, dict):
        if not isinstance(live, dict):
            return copy.deepcopy(template)
        result = copy.deepcopy(live)
        for key, t_val in template.items():
            if key in result:
                result[key] = reconcile_config_to_template(t_val, result[key])
            else:
                result[key] = copy.deepcopy(t_val)
        return result

    if isinstance(template, list):
        if not isinstance(live, list):
            return copy.deepcopy(template)
        result = copy.deepcopy(live)
        for t_item in template:
            if isinstance(t_item, dict) and isinstance(t_item.get("id"), str):
                t_id = t_item["id"]
                idx = next(
                    (
                        i
                        for i, e in enumerate(result)
                        if isinstance(e, dict) and e.get("id") == t_id
                    ),
                    None,
                )
                if idx is None:
                    result.append(copy.deepcopy(t_item))
                else:
                    result[idx] = reconcile_config_to_template(t_item, result[idx])
            elif isinstance(t_item, dict):
                if not any(
                    isinstance(e, dict)
                    and not template_conformance_issues(t_item, e, "")
                    for e in result
                ):
                    result.append(copy.deepcopy(t_item))
            else:
                if t_item not in result:
                    result.append(copy.deepcopy(t_item))
        return result

    # Scalar leaf (including bool): the template value wins.
    return copy.deepcopy(template)
