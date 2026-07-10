#!/usr/bin/env python3
"""Local model server wiring helpers (v1.0.0 Phase 3, items B2/B4).

One shared implementation for the three consumers of the ``LOCAL_MODEL_URL``
contract:

  * The container entrypoint (``deploy/entrypoint.sh``): when ``LOCAL_MODEL_URL``
    is set it satisfies the provider gate (no setup mode) and the boot merges a
    ``models.providers.local`` entry into ``openclaw.json`` built by
    :func:`build_local_provider_entry`. In setup mode (no cloud key, no URL) the
    boot runs :func:`discover_local_servers` and prints the exact
    ``LOCAL_MODEL_URL=`` line to add for anything it finds.
  * The UI server (``GET /api/setup/local-models``): probes the docker bridge
    host from the setup screen so a keyless first boot can offer a detected
    local server as a one-click alternative to a cloud key.
  * The doctor (``provider_key`` check): a configured ``LOCAL_MODEL_URL`` counts
    as an available provider, so a local-only install reads healthy.

Detection is deliberately conservative: a port only counts as a local model
server when ``GET <base>/v1/models`` answers with an OpenAI-compatible model
list. Port 8080 in particular is far too common to trust a bare TCP connect.
Detection never auto-wires anything; wiring requires the explicit
``LOCAL_MODEL_URL`` (env or the setup screen writing it), because role
assignment via the ``*_MODEL`` knobs is still the user's call.

Generated model entries are enriched, not bare: family-specific probes
(:func:`probe_model_metadata`) fill ``contextWindow``/``reasoning``/``input``
where the server exposes them, ``maxTokens`` and ``input`` always get working
defaults (:func:`derive_max_tokens`; text+image), the merge preserves
hand-tuned per-model fields across boots, and the ``LOCAL_MODEL_MAX_TOKENS`` /
``LOCAL_MODEL_REASONING`` / ``LOCAL_MODEL_CONTEXT_WINDOW`` / ``LOCAL_MODEL_VISION``
overrides (:func:`apply_local_model_overrides`) have the last word. A bare
``{id, name}`` entry runs crippled — OpenClaw's 8192-token fallback plus an
undeclared reasoning channel truncate real pipeline turns, and a missing
``input`` degrades the vision-dependent executor and reviewer.

Stdlib-only on purpose: the doctor imports this module, and the entrypoint
calls it from a bare ``python3`` heredoc before any venv exists.
"""

from __future__ import annotations

import copy
import json
import urllib.error
import urllib.request

# Provider id the generated entry lands under (models.providers.local); model
# references in the *_MODEL knobs are then "local/<model-id>".
LOCAL_PROVIDER_ID = "local"

# Known local OpenAI-compatible servers and their default ports, probed in this
# order by discover_local_servers().
KNOWN_LOCAL_SERVERS: tuple[tuple[int, str], ...] = (
    (11434, "Ollama"),
    (8080, "llama.cpp"),
    (1234, "LM Studio"),
)

# Per-request probe bound, seconds. Small on purpose: three ports are probed
# sequentially and a firewalled (packet-dropping) host eats the full timeout.
DEFAULT_PROBE_TIMEOUT = 2.0

# The docker host as seen from inside the container (wired via extra_hosts in
# docker-compose.yml). Bare-metal installs pass their own host or leave the
# feature unconfigured.
DEFAULT_BRIDGE_HOST = "host.docker.internal"

# Per-turn output budgets written onto every generated local model entry.
# OpenClaw's fallback when maxTokens is absent (8192) truncates reasoning models
# mid-turn on non-trivial pipeline phases. With a KNOWN context window the
# budget is half the window capped at DERIVED_MAX_TOKENS_CAP (32768 — the
# pipeline-comfortable ceiling, only reached on 64k+ windows, i.e. hosts that
# can afford it). With an UNKNOWN window the conservative DEFAULT_MAX_TOKENS
# applies: requesting 32k output from a server whose window we could not read
# risks a hard request error on small-context hosts.
DEFAULT_MAX_TOKENS = 16384
DERIVED_MAX_TOKENS_CAP = 32768

# Ollama metadata is one POST /api/show per model; bound the sequential probes
# so a large model library on a slow host cannot stall a setup-screen request.
METADATA_PROBE_MAX_MODELS = 8

# The one honest prerequisite when nothing answers: a server bound to the
# host's 127.0.0.1 is invisible to containers. Shared so the entrypoint, the
# setup screen, and the docs all say the same thing.
BIND_HINT = (
    "a local model server must listen on 0.0.0.0 (or the docker bridge "
    "interface), not 127.0.0.1, to be reachable from the container; "
    "firewall it from everything else"
)


def normalize_local_base_url(url: str) -> str:
    """Return ``url`` normalized to an OpenAI-compatible base ending in ``/v1``.

    Accepts the forms a user plausibly types: with or without a scheme
    (``http://`` is assumed), with or without a trailing ``/`` or ``/v1``.
    Raises ``ValueError`` on an empty value, a non-http(s) scheme, or a URL
    with no host.
    """
    value = (url or "").strip()
    if not value:
        raise ValueError("LOCAL_MODEL_URL is empty")
    if "://" not in value:
        value = "http://" + value
    scheme, _, rest = value.partition("://")
    if scheme not in ("http", "https"):
        raise ValueError(f"LOCAL_MODEL_URL must be http(s), got {scheme}://")
    if not rest or rest.startswith("/"):
        raise ValueError("LOCAL_MODEL_URL has no host")
    value = value.rstrip("/")
    if not value.endswith("/v1"):
        value += "/v1"
    return value


def _request_json(url: str, timeout: float, payload: dict = None):
    """Bounded GET (or POST when ``payload`` is given) returning parsed JSON or None."""
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url, data=body, headers=headers, method="POST" if payload is not None else "GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(1024 * 1024)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None


def probe_openai_models(base_url: str, timeout: float = DEFAULT_PROBE_TIMEOUT):
    """Return the model-id list from ``GET <base_url>/models``, or ``None``.

    ``base_url`` must already end in ``/v1`` (use
    :func:`normalize_local_base_url`). ``None`` means "not a usable
    OpenAI-compatible server": no answer, non-JSON, or a JSON shape without the
    ``data`` model list. Never raises; an answering server with zero models
    returns ``[]`` (usable, nothing loaded yet).
    """
    payload = _request_json(base_url.rstrip("/") + "/models", timeout)
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    models = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            models.append(item["id"].strip())
    return models


def discover_local_servers(
    host: str = DEFAULT_BRIDGE_HOST, timeout: float = DEFAULT_PROBE_TIMEOUT
) -> list:
    """Best-effort probe of the known local-server ports on ``host``.

    Returns a list of ``{"name", "url", "base_url", "models"}`` dicts, one per
    port whose ``/v1/models`` answered with an OpenAI-compatible model list.
    ``url`` is the origin form the user puts in ``LOCAL_MODEL_URL``;
    ``base_url`` is the normalized ``.../v1`` form. Never raises.
    """
    found = []
    for port, name in KNOWN_LOCAL_SERVERS:
        origin = f"http://{host}:{port}"
        base = origin + "/v1"
        models = probe_openai_models(base, timeout=timeout)
        if models is None:
            continue
        found.append({"name": name, "url": origin, "base_url": base, "models": models})
    return found


def _origin_from_base(base_url: str) -> str:
    """Strip the trailing ``/v1`` so family endpoints (``/props``, ``/api/...``) resolve."""
    base = (base_url or "").rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def _probe_lmstudio_metadata(origin: str, timeout: float):
    """LM Studio ``GET /api/v0/models``: context window + vision per model, or None."""
    payload = _request_json(origin + "/api/v0/models", timeout)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return None
    out = {}
    for item in payload["data"]:
        if not (isinstance(item, dict) and isinstance(item.get("id"), str)):
            continue
        ctx = item.get("max_context_length")
        out[item["id"]] = {
            "context_window": ctx if isinstance(ctx, int) and ctx > 0 else None,
            "reasoning": None,  # not exposed by the v0 REST API
            "vision": item.get("type") == "vlm",
        }
    return out or None


def _probe_ollama_metadata(origin: str, model_ids, timeout: float):
    """Ollama ``POST /api/show`` per model: context/reasoning/vision, or None.

    Gated on ``GET /api/version`` so other servers are never spammed with one
    POST per model. Capabilities are definitive when present ("thinking",
    "vision"); context comes from the ``<arch>.context_length`` model_info key.
    """
    version = _request_json(origin + "/api/version", timeout)
    if not isinstance(version, dict) or "version" not in version:
        return None
    out = {}
    for mid in list(model_ids)[:METADATA_PROBE_MAX_MODELS]:
        show = _request_json(origin + "/api/show", timeout, payload={"model": mid})
        if not isinstance(show, dict):
            continue
        caps = show.get("capabilities")
        info = show.get("model_info")
        ctx = None
        if isinstance(info, dict):
            for key, value in info.items():
                if key.endswith(".context_length") and isinstance(value, int) and value > 0:
                    ctx = value
                    break
        out[mid] = {
            "context_window": ctx,
            "reasoning": ("thinking" in caps) if isinstance(caps, list) else None,
            "vision": ("vision" in caps) if isinstance(caps, list) else None,
        }
    return out or None


def _probe_llamacpp_metadata(origin: str, model_ids, timeout: float):
    """llama.cpp ``GET /props``: slot context (+ vision on newer builds), or None.

    One model is loaded per server, so the probed values apply to every listed
    id. Reasoning is not exposed; it stays unknown for the caller to confirm.
    """
    props = _request_json(origin + "/props", timeout)
    if not isinstance(props, dict):
        return None
    settings = props.get("default_generation_settings")
    ctx = settings.get("n_ctx") if isinstance(settings, dict) else None
    modalities = props.get("modalities")
    vision = modalities.get("vision") if isinstance(modalities, dict) else None
    if not isinstance(ctx, int) or ctx <= 0:
        ctx = None
    meta = {
        "context_window": ctx,
        "reasoning": None,
        "vision": vision if isinstance(vision, bool) else None,
    }
    if ctx is None and meta["vision"] is None:
        return None
    return {mid: dict(meta) for mid in model_ids}


def probe_model_metadata(base_url: str, model_ids, timeout: float = DEFAULT_PROBE_TIMEOUT) -> dict:
    """Best-effort per-model metadata from the server's family-specific endpoint.

    Returns ``{model_id: {"context_window": int|None, "reasoning": bool|None,
    "vision": bool|None}}`` — ``None`` marks "unknown", never guessed. Tries LM
    Studio, Ollama, then llama.cpp (disjoint endpoints; first hit wins). An
    unrecognized server returns ``{}``. Never raises.
    """
    ids = [m for m in (model_ids or []) if isinstance(m, str) and m.strip()]
    if not ids:
        return {}
    origin = _origin_from_base(base_url)
    for probe in (
        lambda: _probe_lmstudio_metadata(origin, timeout),
        lambda: _probe_ollama_metadata(origin, ids, timeout),
        lambda: _probe_llamacpp_metadata(origin, ids, timeout),
    ):
        found = probe()
        if found:
            return found
    return {}


def derive_max_tokens(context_window) -> int:
    """Per-turn output budget: half a known context capped at 32k, else 16k."""
    if isinstance(context_window, int) and context_window // 2 > 0:
        return min(DERIVED_MAX_TOKENS_CAP, context_window // 2)
    return DEFAULT_MAX_TOKENS


def build_local_provider_entry(base_url: str, model_ids, metadata: dict = None) -> dict:
    """Build the ``models.providers.local`` entry for ``openclaw.json``.

    ``apiKey: "no-key"`` is mandatory on every local provider entry: without it
    OpenClaw inherits the cloud auth profile and silently falls back to a cloud
    model (see deploy/README.md "Local models on the host").

    Every model entry carries ``maxTokens`` (derived via :func:`derive_max_tokens`
    — never OpenClaw's crippling 8192 fallback) and ``input``: probed vision maps
    to text+image / text, and UNKNOWN vision defaults to ``["text", "image"]`` —
    the executor, reviewer, and prd-creator require image input, and a missing
    ``input`` key degrades them; a genuinely text-only model fails visibly at
    the model call instead. ``contextWindow`` and ``reasoning`` are added only when ``metadata``
    (from :func:`probe_model_metadata`) knows them; those unknowns are omitted,
    not guessed.
    """
    models = []
    for mid in model_ids or []:
        if not (isinstance(mid, str) and mid.strip()):
            continue
        md = (metadata or {}).get(mid) or {}
        model = {
            "id": mid,
            "name": mid,
            "maxTokens": derive_max_tokens(md.get("context_window")),
            "input": ["text"] if md.get("vision") is False else ["text", "image"],
        }
        ctx = md.get("context_window")
        if isinstance(ctx, int) and ctx > 0:
            model["contextWindow"] = ctx
        if isinstance(md.get("reasoning"), bool):
            model["reasoning"] = md["reasoning"]
        models.append(model)
    return {
        "baseUrl": normalize_local_base_url(base_url),
        "api": "openai-completions",
        "apiKey": "no-key",
        "models": models,
    }


def parse_positive_int(value):
    """Parse an env-style override to a positive int, else None (unset/garbage)."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def parse_bool_flag(value):
    """Parse an env-style flag to a bool, else None (unset/garbage)."""
    text = str(value or "").strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return None


def apply_local_model_overrides(
    entry: dict,
    *,
    max_tokens=None,
    reasoning=None,
    context_window=None,
    vision=None,
    target_ids=None,
) -> dict:
    """Return a copy of ``entry`` with explicit user overrides applied.

    Overrides are the last word — they beat both probed values and prior
    hand-edits (same doctrine as the ``*_MODEL`` env knobs). ``target_ids``
    limits them to the role-referenced models; empty/None applies to all.
    ``vision`` maps to the ``input`` field. An explicit ``context_window``
    with no explicit ``max_tokens`` re-derives the output budget from it.
    """
    out = copy.deepcopy(entry) if isinstance(entry, dict) else {"models": []}
    targets = {t for t in (target_ids or []) if isinstance(t, str) and t}
    for model in out.get("models") or []:
        if not isinstance(model, dict):
            continue
        if targets and model.get("id") not in targets:
            continue
        if isinstance(context_window, int) and context_window > 0:
            model["contextWindow"] = context_window
            if not (isinstance(max_tokens, int) and max_tokens > 0):
                model["maxTokens"] = derive_max_tokens(context_window)
        if isinstance(max_tokens, int) and max_tokens > 0:
            model["maxTokens"] = max_tokens
        if isinstance(reasoning, bool):
            model["reasoning"] = reasoning
        if isinstance(vision, bool):
            model["input"] = ["text", "image"] if vision else ["text"]
    return out


def summarize_model_entry(model: dict) -> str:
    """One-line human summary of a generated model entry (boot-log / setup surfaces)."""
    ctx = model.get("contextWindow")
    reasoning = model.get("reasoning")
    parts = [
        f"maxTokens={model.get('maxTokens', 'unset')}",
        f"contextWindow={ctx if ctx else 'unknown'}",
        "reasoning=" + ("on" if reasoning is True else "off" if reasoning is False else "unset"),
    ]
    if "image" in (model.get("input") or []):
        parts.append("vision")
    return ", ".join(parts)


def merge_local_provider(config: dict, entry: dict) -> dict:
    """Return a copy of ``config`` with the local provider entry installed.

    Creates the ``models.providers`` path when absent, forces ``apiKey:
    "no-key"``, and preserves everything else untouched. The model list mirrors
    ``entry`` (the live server), but a model whose id already exists in the
    prior ``local`` entry keeps its existing fields — a hand-tuned or
    setup-screen-enriched ``maxTokens``/``reasoning``/``contextWindow`` must
    survive the per-boot regeneration, with ``entry`` only filling gaps.
    Explicit env overrides beat this (see :func:`apply_local_model_overrides`,
    applied after the merge). ``config`` is never mutated.
    """
    result = copy.deepcopy(config) if isinstance(config, dict) else {}
    models = result.setdefault("models", {})
    if not isinstance(models, dict):
        models = {}
        result["models"] = models
    providers = models.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        models["providers"] = providers
    installed = copy.deepcopy(entry)
    installed["apiKey"] = "no-key"
    prior = providers.get(LOCAL_PROVIDER_ID)
    if isinstance(prior, dict):
        prior_models = {
            m["id"]: m
            for m in (prior.get("models") or [])
            if isinstance(m, dict) and isinstance(m.get("id"), str)
        }
        installed["models"] = [
            {**m, **copy.deepcopy(prior_models[m["id"]])}
            if isinstance(m, dict) and m.get("id") in prior_models
            else m
            for m in (installed.get("models") or [])
        ]
    providers[LOCAL_PROVIDER_ID] = installed
    return result
