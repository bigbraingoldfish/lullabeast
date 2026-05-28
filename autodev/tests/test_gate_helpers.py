"""P1 Stage D — Hygiene H1 — shared gate-script helpers in utils.py.

Two predicates that gates use to decide "does this phase require contract-shape
verification of kind X" live in `autodev/pipeline/gate_scripts/utils.py` after
the H1 extraction:

- ``phase_has_behavioral_block(current_phase)`` — True iff the phase's
  ``behavioral_verification`` dict carries all three sub-fields populated.
  Previously duplicated as ``_requires_behavioral_verification`` in
  reviewer_gate.py and ``_phase_has_behavioral_block`` in executor_gate.py;
  H1 collapses them into one shared utility because P1 Stage D adds a third
  caller (``requires_regression_verification``) and the maintenance calculus
  changed.

- ``requires_regression_verification(current_phase)`` — True iff the phase
  carries both ``prior_phase_raw_id`` and ``prior_phase_how_to_check`` (both
  truthy). Defined alongside the behavioural predicate so all "does this phase
  need contract-shape verification of kind X" predicates live in one place.

These are direct unit tests on the shared utilities. Existing gate-side
behavioural tests still pass unchanged — they exercise call sites, not these
helper internals.
"""

import pytest

# Path wiring handled by autodev/tests/conftest.py
import utils as utils_module


# ---------------------------------------------------------------------------
# phase_has_behavioral_block
# ---------------------------------------------------------------------------


class TestPhaseHasBehavioralBlock:
    """§3 File 1, rows 1–4. Mirror of the prior gate-local
    ``_requires_behavioral_verification`` contract — populated block True;
    None / partial / non-dict all False."""

    def test_phase_has_behavioral_block_returns_true_for_populated_block(self):
        """All three sub-fields populated → True (the only True case)."""
        current_phase = {
            "raw_id": "CORE-E1",
            "behavioral_verification": {
                "user_observable": "User sees task list on /tasks",
                "how_to_check": "Navigate to /tasks; expect ≥1 row rendered",
                "failure_language": "The /tasks page does not load",
            },
        }
        assert utils_module.phase_has_behavioral_block(current_phase) is True

    def test_phase_has_behavioral_block_returns_false_for_none(self):
        """Pre-P0 in-flight phases have behavioral_verification: None — the
        utility must agree with the gate-local helpers (transitional rule
        from P0 §2.9)."""
        current_phase = {
            "raw_id": "CORE-E1",
            "behavioral_verification": None,
        }
        assert utils_module.phase_has_behavioral_block(current_phase) is False

    def test_phase_has_behavioral_block_returns_false_for_missing_key(self):
        """current_phase has no ``behavioral_verification`` key at all —
        distinct path from explicit None (both hit the same isinstance
        guard, but the gate-local predecessor's tests covered both cases
        so the migrated helper preserves the coverage)."""
        current_phase = {"raw_id": "CORE-E1"}
        assert utils_module.phase_has_behavioral_block(current_phase) is False

    def test_phase_has_behavioral_block_returns_false_for_partial_block(self):
        """Missing one of the three required sub-fields → False. The gate
        does not pretend a partial block is usable; preflight is the gate
        that enforces completeness."""
        current_phase = {
            "raw_id": "CORE-E1",
            "behavioral_verification": {
                "user_observable": "x",
                "how_to_check": "y",
                # failure_language intentionally missing
            },
        }
        assert utils_module.phase_has_behavioral_block(current_phase) is False

    def test_phase_has_behavioral_block_returns_false_for_non_dict_input(self):
        """Non-dict or None top-level input → False. Defensive against a
        future caller that passes the raw phase number or a list."""
        assert utils_module.phase_has_behavioral_block(None) is False
        assert utils_module.phase_has_behavioral_block([]) is False
        assert utils_module.phase_has_behavioral_block("CORE-E1") is False


# ---------------------------------------------------------------------------
# requires_regression_verification
# ---------------------------------------------------------------------------


class TestRequiresRegressionVerification:
    """§3 File 1, rows 5–9. Both prior_phase_* fields must be truthy for
    True; anything else → False. Pins the predicate that drives the new
    REGRESSION_UNVERIFIED branch."""

    def test_requires_regression_verification_true_when_both_fields_populated(self):
        """Happy path: prior phase resolved AND has a how_to_check recipe."""
        current_phase = {
            "raw_id": "CORE-E2",
            "prior_phase_raw_id": "CORE-E1",
            "prior_phase_how_to_check": "Navigate to /tasks; expect ≥1 row",
        }
        assert utils_module.requires_regression_verification(current_phase) is True

    def test_requires_regression_verification_false_when_only_raw_id_populated(self):
        """Predecessor exists but had no how_to_check recipe (legacy phase
        with no behavioural block). Resolver still populates raw_id but
        leaves how_to_check as None — nothing to regress against."""
        current_phase = {
            "raw_id": "CORE-E2",
            "prior_phase_raw_id": "CORE-E1",
            "prior_phase_how_to_check": None,
        }
        assert utils_module.requires_regression_verification(current_phase) is False

    def test_requires_regression_verification_false_when_only_how_to_check_populated(self):
        """Symmetric defensive case — should never happen via the resolver
        but the predicate must not be fooled by a hand-edited current_phase."""
        current_phase = {
            "raw_id": "CORE-E2",
            "prior_phase_raw_id": None,
            "prior_phase_how_to_check": "Navigate to /tasks",
        }
        assert utils_module.requires_regression_verification(current_phase) is False

    def test_requires_regression_verification_false_when_both_missing(self):
        """First phase, or all predecessors were blocked/skipped — neither
        key present in the dict. Backward-compatible with pre-Stage-D
        current_phase.json shapes."""
        current_phase = {"raw_id": "CORE-E1"}
        assert utils_module.requires_regression_verification(current_phase) is False

    def test_requires_regression_verification_false_for_non_dict_input(self):
        """Defensive: None / list / string → False. Catches a future caller
        that passes the wrong shape."""
        assert utils_module.requires_regression_verification(None) is False
        assert utils_module.requires_regression_verification([]) is False
        assert utils_module.requires_regression_verification("CORE-E1") is False
