#!/usr/bin/env python3
"""Dashboard-owned model property overrides (the overlay file).

Per-boot reconcile force-wins every scalar the golden template declares
(``openclaw_template.reconcile_config_to_template``), so a dashboard edit to a
shipped model's ``cost``/``contextWindow``/``input``/params written directly
into ``openclaw.json`` would be reverted on the next boot. Edits therefore
persist in a Lullabeast-owned overlay file on the data volume
(``/data/model-overrides.json``, path seeded into ``ui/config.json`` as
``model_overrides_path``) and are re-applied on top of the rendered config by
the entrypoint's apply path, after render/reconcile and before local-model
wiring. Precedence, lowest to highest: template render -> this overlay ->
explicit ``LOCAL_MODEL_*`` env values (local models only).

File shape: ``{"models": {"<provider>/<model-id>": {<properties>}}}``.
Editable properties: ``input`` (modalities), ``contextWindow``, ``maxTokens``,
``cost`` (per-key), ``reasoning``, and ``params`` (``temperature``/``top_p``,
applied to ``agents.defaults.models``). An override naming a model that is no
longer registered stays dormant in the file and is skipped at apply time.

Stdlib-only: the container entrypoint imports this before the app venv is
guaranteed importable.
"""

from __future__ import annotations

import copy
import json
import math

COST_KEYS = ("input", "output", "cacheRead", "cacheWrite")
PARAM_KEYS = ("temperature", "top_p")
INPUT_MODALITIES = ("text", "image", "video", "audio")
# Same sanity ceilings as the local-model setup confirm fields (2^20 / 2^24).
MAX_TOKENS_CEILING = 1_048_576
CONTEXT_WINDOW_CEILING = 16_777_216
# USD per M tokens; far above any real price, low enough to reject nonsense.
COST_CEILING = 100_000


def _is_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_positive_int(value, ceiling: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= ceiling


def load_model_overrides(path: str) -> dict:
    """Read the overlay file and return its ``models`` map.

    Read-tolerant: a missing, unreadable, or malformed file returns ``{}`` (the
    overlay is an optional layer; a corrupt one must never fail a boot). Only
    dict-valued entries keyed by a ``provider/model`` string survive.
    """
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        return {}
    return {
        ref: props
        for ref, props in models.items()
        if isinstance(ref, str) and "/" in ref and isinstance(props, dict)
    }


def validate_model_override(props) -> list[str]:
    """Boundary validation for one model's override properties.

    Returns human-readable error strings (empty list = valid). ``None`` as a
    field value means "clear this override" and is always accepted; numeric
    fields reject bools and non-finite values (Python's json parser accepts
    NaN/Infinity, which must never reach the config).
    """
    if not isinstance(props, dict) or not props:
        return ["override must be a non-empty object"]
    errors: list[str] = []
    for key, value in props.items():
        if value is None:
            continue
        if key == "input":
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(m, str) or m not in INPUT_MODALITIES for m in value)
            ):
                errors.append(
                    "input must be a non-empty list drawn from "
                    + ", ".join(INPUT_MODALITIES)
                )
            elif "text" not in value:
                errors.append("input must include text")
        elif key == "contextWindow":
            if not _is_positive_int(value, CONTEXT_WINDOW_CEILING):
                errors.append(
                    f"contextWindow must be an integer between 1 and {CONTEXT_WINDOW_CEILING}"
                )
        elif key == "maxTokens":
            if not _is_positive_int(value, MAX_TOKENS_CEILING):
                errors.append(
                    f"maxTokens must be an integer between 1 and {MAX_TOKENS_CEILING}"
                )
        elif key == "reasoning":
            if not isinstance(value, bool):
                errors.append("reasoning must be a boolean")
        elif key == "cost":
            if not isinstance(value, dict) or not value:
                errors.append("cost must be a non-empty object")
                continue
            for ckey, cval in value.items():
                if ckey not in COST_KEYS:
                    errors.append(
                        "cost keys must be one of " + ", ".join(COST_KEYS)
                    )
                elif cval is not None and not (
                    _is_number(cval) and 0 <= cval <= COST_CEILING
                ):
                    errors.append(
                        f"cost.{ckey} must be a number between 0 and {COST_CEILING}"
                    )
        elif key == "params":
            if not isinstance(value, dict) or not value:
                errors.append("params must be a non-empty object")
                continue
            for pkey, pval in value.items():
                if pkey not in PARAM_KEYS:
                    errors.append(
                        "params keys must be one of " + ", ".join(PARAM_KEYS)
                    )
                elif pval is None:
                    continue
                elif pkey == "temperature" and not (_is_number(pval) and 0 <= pval <= 2):
                    errors.append("params.temperature must be a number between 0 and 2")
                elif pkey == "top_p" and not (_is_number(pval) and 0 < pval <= 1):
                    errors.append("params.top_p must be a number above 0 and at most 1")
        else:
            errors.append(f"unknown property {key}")
    return errors


def split_valid_overrides(overrides: dict) -> tuple[dict, dict]:
    """Partition an overrides map into ``(valid, dropped)``.

    Each entry is checked with :func:`validate_model_override`, the same gate the
    dashboard API applies; ``dropped`` maps every rejected ref to its errors. The
    boot applies only ``valid`` so a hand-edited or upgrade-invalidated override
    cannot reach the gateway config and crash-loop the boot.
    """
    valid: dict = {}
    dropped: dict = {}
    for ref, props in overrides.items():
        errors = validate_model_override(props)
        if errors:
            dropped[ref] = errors
        else:
            valid[ref] = props
    return valid, dropped


def _merge_props(existing: dict, updates: dict) -> dict:
    """One model's override entry with ``updates`` merged in.

    ``None`` clears a field (or a ``cost``/``params`` sub-key); dict fields
    merge per key. Empty sub-dicts are dropped so a fully cleared entry
    disappears instead of lingering as ``{}``.
    """
    merged = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    for key, value in updates.items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict) and key in ("cost", "params"):
            sub = dict(merged.get(key) or {})
            for skey, sval in value.items():
                if sval is None:
                    sub.pop(skey, None)
                else:
                    sub[skey] = sval
            if sub:
                merged[key] = sub
            else:
                merged.pop(key, None)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def merge_model_overrides(existing: dict, updates: dict) -> dict:
    """The overlay ``models`` map with a per-model ``updates`` map merged in.

    A model whose merged entry ends up empty is removed. Neither input is
    mutated.
    """
    result = {ref: copy.deepcopy(props) for ref, props in (existing or {}).items()}
    for ref, props in (updates or {}).items():
        merged = _merge_props(result.get(ref, {}), props)
        if merged:
            result[ref] = merged
        else:
            result.pop(ref, None)
    return result


def apply_model_overrides(config: dict, overrides: dict) -> dict:
    """An openclaw.json-shaped ``config`` with the overlay applied.

    Provider-entry properties (``input``/``contextWindow``/``maxTokens``/
    ``cost``/``reasoning``) land on the matching ``models.providers.<p>.models[]``
    entry; ``params`` land on ``agents.defaults.models["<p>/<id>"].params``
    (created when absent). A ref whose model is not registered is skipped
    entirely. ``config`` is returned as-is when there is nothing to apply and
    is never mutated otherwise.
    """
    if not overrides or not isinstance(config, dict):
        return config
    result = copy.deepcopy(config)
    providers = (result.get("models") or {}).get("providers")
    if not isinstance(providers, dict):
        providers = {}
    for ref, props in overrides.items():
        if not isinstance(props, dict):
            continue
        provider_name, _, model_id = ref.partition("/")
        provider = providers.get(provider_name)
        entry = None
        if isinstance(provider, dict):
            for m in provider.get("models") or []:
                if isinstance(m, dict) and m.get("id") == model_id:
                    entry = m
                    break
        if entry is None:
            continue
        for key in ("input", "contextWindow", "maxTokens", "reasoning"):
            if key in props and props[key] is not None:
                entry[key] = copy.deepcopy(props[key])
        cost = props.get("cost")
        if isinstance(cost, dict) and cost:
            merged_cost = dict(entry.get("cost") or {})
            merged_cost.update({k: v for k, v in cost.items() if v is not None})
            entry["cost"] = merged_cost
        params = props.get("params")
        if isinstance(params, dict) and params:
            agents = result.setdefault("agents", {})
            defaults = agents.setdefault("defaults", {}) if isinstance(agents, dict) else {}
            models_map = defaults.setdefault("models", {}) if isinstance(defaults, dict) else {}
            if isinstance(models_map, dict):
                model_entry = models_map.setdefault(ref, {})
                if isinstance(model_entry, dict):
                    entry_params = model_entry.setdefault("params", {})
                    if isinstance(entry_params, dict):
                        entry_params.update(
                            {k: v for k, v in params.items() if v is not None}
                        )
    return result
