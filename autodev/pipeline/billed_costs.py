"""Billed costs for OpenRouter calls, settled after the fact.

OpenClaw prices every model response from the model's configured list price.
For OpenRouter it then asks the generation endpoint for the billed cost and
keeps the list-price figure when that lookup fails. The lookup runs the moment
the response ends, with a 2 s timeout, while OpenRouter publishes a
generation's record later (from 82 s to more than 3 minutes in measurements on
2026-11-03), so it fails and the session records carry list prices. OpenRouter
often routes to a cheaper endpoint than the list price assumes: on 2026-11-03 a
DeepSeek V4.1 Flash run tracked $2.67 while OpenRouter billed $0.54 for the
whole night.

This module is the shared, stdlib-only logic behind the fix. The orchestrator
classifies each response's cost (``classify_message_cost``) and records, on the
phase's metrics row, which OpenRouter generations are still priced at list
(``cost_pending``: generation id -> list-price cost). The dashboard server
looks those ids up once OpenRouter has them (``lookup_generation_costs``),
keeps the billed costs in a cache, and settles every row it reads
(``settle_row``). Whatever is still at list price stays counted in
``cost_estimated``, so the dashboard can label it as an estimate.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

OPENROUTER_PROVIDER = "openrouter"
GENERATION_URL = "https://openrouter.ai/api/v1/generation"
ROLE_TOKEN_KEYS = ("planner_tokens", "executor_tokens", "reviewer_tokens")
# The parts OpenClaw sums into usage.cost.total, in its summation order.
_COST_PARTS = ("input", "output", "cacheRead", "cacheWrite")


def _number(value) -> float:
    """A finite float, or 0.0 for anything else (bools included)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    value = float(value)
    return value if math.isfinite(value) else 0.0


def classify_message_cost(message) -> tuple[float, bool, str | None]:
    """``(cost, estimated, generation_id)`` for one assistant message of an OpenClaw session.

    ``cost`` is ``usage.cost.total``. OpenClaw sets it to the sum of the
    message's input, output, cacheRead and cacheWrite list-price parts, and
    replaces it with OpenRouter's billed cost only when its own generation
    lookup succeeds. So an OpenRouter total that no longer equals the sum of
    its parts is billed, and any other nonzero cost is a list-price estimate.
    ``generation_id`` is the ``responseId`` of an estimated OpenRouter response
    (``gen-...``): the key for looking up its billed cost later. A zero cost is
    never an estimate.
    """
    usage = message.get("usage") if isinstance(message, dict) else None
    cost = usage.get("cost") if isinstance(usage, dict) else None
    if not isinstance(cost, dict):
        return 0.0, False, None
    total = _number(cost.get("total"))
    if total == 0.0:
        return 0.0, False, None
    openrouter = message.get("provider") == OPENROUTER_PROVIDER
    if openrouter and all(part in cost for part in _COST_PARTS):
        # Left to right with +=, as OpenClaw sums them. Not sum(): it compensates
        # since Python 3.12 and can differ from OpenClaw's total in the last bit.
        parts = 0.0
        for part in _COST_PARTS:
            parts += _number(cost.get(part))
        if total != parts:
            return total, False, None
    generation_id = message.get("responseId")
    if not (openrouter and isinstance(generation_id, str) and generation_id.startswith("gen-")):
        generation_id = None
    return total, True, generation_id


def settle_tokens(tokens, billed):
    """One role's token dict with each pending generation that has a billed cost settled.

    ``cost_pending`` maps a generation id to the list-price cost counted for
    it in ``cost_total`` and ``cost_estimated``. Each id found in ``billed``
    swaps that estimate for the billed cost and leaves the pending map.
    Returns ``tokens`` itself when nothing settles; never mutates it.
    """
    if not isinstance(tokens, dict) or not billed:
        return tokens
    pending = tokens.get("cost_pending")
    if not isinstance(pending, dict):
        return tokens
    settled = {gid: est for gid, est in pending.items() if gid in billed}
    if not settled:
        return tokens
    out = dict(tokens)
    estimates = sum(_number(est) for est in settled.values())
    billed_sum = sum(_number(billed[gid]) for gid in settled)
    out["cost_total"] = _number(tokens.get("cost_total")) - estimates + billed_sum
    # Rounded so a fully settled role reads exactly 0.0, not float residue.
    out["cost_estimated"] = round(max(0.0, _number(tokens.get("cost_estimated")) - estimates), 9)
    remaining = {gid: est for gid, est in pending.items() if gid not in settled}
    if remaining:
        out["cost_pending"] = remaining
    else:
        out.pop("cost_pending", None)
    return out


def settle_row(row, billed):
    """A metrics row with every role settled against ``billed``.

    Recomputes the row's ``cost_total`` (rounded to 6 places, as the writer
    does) and ``cost_estimated`` from the settled roles. Returns ``row``
    itself when nothing settles; never mutates it.
    """
    if not isinstance(row, dict) or not billed:
        return row
    out = None
    for key in ROLE_TOKEN_KEYS:
        tokens = row.get(key)
        settled = settle_tokens(tokens, billed)
        if settled is not tokens:
            if out is None:
                out = dict(row)
            out[key] = settled
    if out is None:
        return row
    roles = [out[key] for key in ROLE_TOKEN_KEYS if isinstance(out.get(key), dict)]
    out["cost_total"] = round(sum(_number(r.get("cost_total")) for r in roles), 6)
    out["cost_estimated"] = round(sum(_number(r.get("cost_estimated")) for r in roles), 9)
    return out


def pending_generation_ids(rows) -> list[str]:
    """Every generation id still pending on ``rows``, first-seen order, no duplicates."""
    seen: dict[str, None] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for key in ROLE_TOKEN_KEYS:
            tokens = row.get(key)
            pending = tokens.get("cost_pending") if isinstance(tokens, dict) else None
            if isinstance(pending, dict):
                for gid in pending:
                    if isinstance(gid, str):
                        seen.setdefault(gid, None)
    return list(seen)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect.

    urllib's default opener re-sends all request headers, Authorization
    included, to wherever a redirect points, on any host and over any scheme.
    The generation lookup carries the user's OpenRouter key, so a redirect
    ends the lookup instead: an HTTPError, which reads as "not published yet".
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def lookup_generation_cost(generation_id, api_key, *, timeout=5.0, urlopen=_OPENER.open):
    """OpenRouter's billed ``total_cost`` for one generation, or None.

    None covers a generation OpenRouter has not published yet (404), any
    network or HTTP error, a redirect (never followed: the request carries the
    key), and a response without a usable cost.
    """
    query = urllib.parse.urlencode({"id": generation_id})
    request = urllib.request.Request(
        f"{GENERATION_URL}?{query}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.load(response).get("data")
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return None
    total = data.get("total_cost") if isinstance(data, dict) else None
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        return None
    total = float(total)
    return total if math.isfinite(total) and total >= 0 else None


def lookup_generation_costs(generation_ids, api_key, *, lookup=None, max_workers=8, limit=200):
    """``{generation_id: billed cost}`` for the ids OpenRouter has published.

    Looks up at most ``limit`` distinct ids, ``max_workers`` at a time; ids
    without a billed cost yet are left out, to be tried again later.
    """
    if not api_key:
        return {}
    ids = list(dict.fromkeys(g for g in generation_ids or [] if isinstance(g, str)))[:limit]
    if not ids:
        return {}
    lookup = lookup or lookup_generation_cost
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        costs = list(pool.map(lambda gid: lookup(gid, api_key), ids))
    return {gid: cost for gid, cost in zip(ids, costs) if cost is not None}
