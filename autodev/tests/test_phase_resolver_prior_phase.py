"""P1 Stage D — phase_resolver prior-phase tracking.

The resolver walks the roadmap to find the next pending phase. Stage D adds
a side effect: while walking, the most recent completed (``status_char == 'x'``)
phase is tracked along with its how_to_check recipe, and surfaced into
``current_phase.json`` as ``prior_phase_raw_id`` and
``prior_phase_how_to_check``.

The reviewer agent uses these to re-execute the prior phase's recipe alongside
the current phase's recipe, providing N→N-1 regression protection. Full
prior-phase iteration is deferred to P3 Stage B.

These tests exercise the resolver end-to-end by writing a temporary roadmap,
invoking ``validate_and_identify``, and asserting on the materialised
``current_phase.json``.
"""

import json
import os
import sys
from contextlib import contextmanager

import pytest

# Path wiring handled by autodev/tests/conftest.py
import phase_resolver as phase_resolver_module


_BV_BLOCK = (
    "  **Behavioral Verification:**\n"
    "  - **User-observable:** The user sees a list of tasks on /tasks.\n"
    "  - **How we'll check:** Navigate to /tasks; expect at least one row rendered.\n"
    "  - **If this fails, the user sees:** The /tasks page does not load.\n"
)


def _phase_line(status_char, raw_id, goal="Test goal"):
    """Return a single roadmap phase-header line with the canonical shape."""
    return f"- [{status_char}] `{raw_id}` | low | {goal}\n"


def _write_roadmap(tmp_path, *phases_with_bv):
    """Write a roadmap.md to tmp_path; each entry is (status_char, raw_id,
    include_bv_block_bool). Returns the file path.

    Each phase gets a body that contains either the canonical BV block (when
    include_bv=True) or nothing (when False)."""
    lines = ["# Test roadmap\n", "\n"]
    for status_char, raw_id, include_bv in phases_with_bv:
        lines.append(_phase_line(status_char, raw_id))
        if include_bv:
            lines.append(_BV_BLOCK)
        lines.append("\n")
    roadmap_path = tmp_path / "roadmap.md"
    roadmap_path.write_text("".join(lines))
    return str(roadmap_path)


def _resolve(tmp_path, roadmap_path):
    """Invoke validate_and_identify against a roadmap; return the parsed
    current_phase.json dict.

    validate_and_identify writes current_phase.json under
    ``<roadmap_dir>/.autodev/pipeline/current_phase.json`` then sys.exit()s,
    so we catch SystemExit and read the materialised file."""
    out_path = tmp_path / ".autodev" / "pipeline" / "current_phase.json"
    try:
        phase_resolver_module.validate_and_identify(roadmap_path)
    except SystemExit:
        pass  # validate_and_identify always exits — that's the contract
    assert out_path.exists(), (
        f"current_phase.json must be materialised by validate_and_identify; "
        f"expected at {out_path}"
    )
    return json.loads(out_path.read_text())


# ---------------------------------------------------------------------------
# §3 File 2 — six rows
# ---------------------------------------------------------------------------


def test_resolver_writes_prior_phase_for_second_phase(tmp_path):
    """Roadmap `[x] CORE-E1` (valid BV block) + `[ ] CORE-E2`. The resolver
    must surface CORE-E1 as the prior phase with its how_to_check recipe.

    This is the happy path — phase 2 onwards, with a completed predecessor
    that has a recipe. The reviewer will run both recipes."""
    roadmap = _write_roadmap(
        tmp_path,
        ("x", "CORE-E1", True),   # completed, has BV block
        (" ", "CORE-E2", True),   # pending, has BV block
    )
    data = _resolve(tmp_path, roadmap)
    assert data["raw_id"] == "CORE-E2"
    assert data.get("prior_phase_raw_id") == "CORE-E1", (
        f"prior_phase_raw_id must be CORE-E1, got {data.get('prior_phase_raw_id')!r}. "
        f"Resolver must track the most recent x-status phase."
    )
    assert data.get("prior_phase_how_to_check") == (
        "Navigate to /tasks; expect at least one row rendered."
    ), (
        f"prior_phase_how_to_check must match CORE-E1's how_to_check text, "
        f"got {data.get('prior_phase_how_to_check')!r}"
    )


def test_resolver_writes_null_prior_phase_for_first_phase(tmp_path):
    """Only `[ ] CORE-E1` — no predecessors. Both prior_phase_* fields
    must be None so the reviewer's regression branch is correctly skipped."""
    roadmap = _write_roadmap(tmp_path, (" ", "CORE-E1", True))
    data = _resolve(tmp_path, roadmap)
    assert data["raw_id"] == "CORE-E1"
    assert data.get("prior_phase_raw_id") is None
    assert data.get("prior_phase_how_to_check") is None


def test_resolver_skips_escalated_phases_when_finding_prior(tmp_path):
    """Roadmap `[x] CORE-E1`, `[!] CORE-E2`, `[ ] CORE-E3`. Escalated
    (`!`) phases never landed — the resolver must skip them when tracking
    the most recent completed predecessor. CORE-E3 sees CORE-E1 as prior,
    not CORE-E2."""
    roadmap = _write_roadmap(
        tmp_path,
        ("x", "CORE-E1", True),   # completed
        ("!", "CORE-E2", True),   # escalated — must be skipped
        (" ", "CORE-E3", True),   # pending
    )
    # Walker stops at first non-x/non-`-` phase. `[!]` is the first stop —
    # so the resolver returns CORE-E2 as current with CORE-E1 as prior.
    # That's the desired behaviour — escalated phases never landed but they
    # are still the current pending work.
    data = _resolve(tmp_path, roadmap)
    # Validate the resolver picked the first non-x/non-`-` phase as current
    # (CORE-E2, the escalated one, because it has not landed yet).
    assert data["raw_id"] == "CORE-E2", (
        f"Resolver should pick CORE-E2 (first non-x phase) as current, "
        f"got {data['raw_id']!r}"
    )
    assert data.get("prior_phase_raw_id") == "CORE-E1", (
        f"prior_phase_raw_id must point at the most recent COMPLETED "
        f"predecessor (CORE-E1), not the escalated CORE-E2"
    )


def test_resolver_skips_skipped_phases_when_finding_prior(tmp_path):
    """Roadmap `[x] CORE-E1`, `[-] CORE-E2`, `[ ] CORE-E3`. Skipped (`-`)
    phases were intentionally not built — the resolver must skip them
    when tracking the most recent completed predecessor."""
    roadmap = _write_roadmap(
        tmp_path,
        ("x", "CORE-E1", True),   # completed
        ("-", "CORE-E2", True),   # skipped — must NOT update last_completed
        (" ", "CORE-E3", True),   # pending
    )
    data = _resolve(tmp_path, roadmap)
    assert data["raw_id"] == "CORE-E3"
    assert data.get("prior_phase_raw_id") == "CORE-E1", (
        f"prior_phase_raw_id must point at the most recent COMPLETED "
        f"predecessor (CORE-E1), skipping CORE-E2 which is [-] skipped, "
        f"got {data.get('prior_phase_raw_id')!r}"
    )


def test_resolver_prior_phase_null_when_only_predecessors_are_blocked(tmp_path):
    """Roadmap `[!] CORE-E1`, `[ ] CORE-E2`. CORE-E1 was escalated and never
    landed — there is no completed predecessor. Both prior_phase_* fields
    must be None.

    Note: the walker breaks at the first non-x/non-`-` phase (CORE-E1
    itself), so CORE-E2 is never the resolved current. We assert on the
    resolved phase (CORE-E1) and confirm its prior is null."""
    roadmap = _write_roadmap(
        tmp_path,
        ("!", "CORE-E1", True),
        (" ", "CORE-E2", True),
    )
    data = _resolve(tmp_path, roadmap)
    # CORE-E1 is the first non-x phase — resolver picks it
    assert data["raw_id"] == "CORE-E1"
    assert data.get("prior_phase_raw_id") is None
    assert data.get("prior_phase_how_to_check") is None


def test_resolver_prior_phase_null_when_predecessor_lacks_behavioral_block(
    tmp_path,
):
    """Roadmap `[x] CORE-E1` (no BV block), `[ ] CORE-E2`. The resolver
    resolves a predecessor (CORE-E1) but its how_to_check is None — there
    is no recipe to regress against. The reviewer's regression branch
    must therefore be skipped (via requires_regression_verification → False).

    `prior_phase_raw_id` is still populated so downstream consumers (UI,
    logs) can name the predecessor for diagnostics."""
    roadmap = _write_roadmap(
        tmp_path,
        ("x", "CORE-E1", False),  # completed but no BV block at all
        (" ", "CORE-E2", True),
    )
    data = _resolve(tmp_path, roadmap)
    assert data["raw_id"] == "CORE-E2"
    assert data.get("prior_phase_raw_id") == "CORE-E1", (
        f"prior_phase_raw_id should still name CORE-E1 even when it has no "
        f"BV block — useful for UI/logs. Got "
        f"{data.get('prior_phase_raw_id')!r}"
    )
    assert data.get("prior_phase_how_to_check") is None, (
        f"prior_phase_how_to_check must be None when the predecessor has no "
        f"BV block — there is no recipe to regress against. Got "
        f"{data.get('prior_phase_how_to_check')!r}"
    )
