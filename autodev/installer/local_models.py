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


def probe_openai_models(base_url: str, timeout: float = DEFAULT_PROBE_TIMEOUT):
    """Return the model-id list from ``GET <base_url>/models``, or ``None``.

    ``base_url`` must already end in ``/v1`` (use
    :func:`normalize_local_base_url`). ``None`` means "not a usable
    OpenAI-compatible server": no answer, non-JSON, or a JSON shape without the
    ``data`` model list. Never raises; an answering server with zero models
    returns ``[]`` (usable, nothing loaded yet).
    """
    req = urllib.request.Request(
        base_url.rstrip("/") + "/models",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(1024 * 1024)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None
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


def build_local_provider_entry(base_url: str, model_ids) -> dict:
    """Build the ``models.providers.local`` entry for ``openclaw.json``.

    ``apiKey: "no-key"`` is mandatory on every local provider entry: without it
    OpenClaw inherits the cloud auth profile and silently falls back to a cloud
    model (see deploy/README.md "Local models on the host"). Model entries are
    the minimal ``{id, name}`` shape; the manual openclaw.json edit remains the
    path for full per-model metadata (context windows, image input flags).
    """
    return {
        "baseUrl": normalize_local_base_url(base_url),
        "api": "openai-completions",
        "apiKey": "no-key",
        "models": [
            {"id": mid, "name": mid}
            for mid in (model_ids or [])
            if isinstance(mid, str) and mid.strip()
        ],
    }


def merge_local_provider(config: dict, entry: dict) -> dict:
    """Return a copy of ``config`` with the local provider entry installed.

    Creates the ``models.providers`` path when absent, replaces any prior
    ``local`` entry wholesale (the entry is regenerated each boot from
    ``LOCAL_MODEL_URL``), forces ``apiKey: "no-key"``, and preserves everything
    else untouched. ``config`` is never mutated.
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
    providers[LOCAL_PROVIDER_ID] = installed
    return result
