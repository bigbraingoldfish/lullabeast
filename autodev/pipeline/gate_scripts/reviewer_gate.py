"""Reviewer verdict gate — adjudicates the reviewer's verdict and routes the next step.

Verdict gate (see ./README.md): prints ``PASS`` or a route token (``ROUTE_EXECUTOR`` /
``ROUTE_PLANNER`` / ``ROUTE_ESCALATE`` / ``*_UNVERIFIED`` / ``MISSING_ARTIFACTS`` /
``CONTRACT_FAILURE``) on stdout and **always exits 0**. Enforces the workspace-boundary check
on every evidence path. Deterministic — no LLM, network, or clock.
"""
import os
import sys
import json

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from utils import (
    ARTIFACTS_DIR,
    load_json_safe,
    phase_has_behavioral_block,
    record_error_code_only,
    requires_regression_verification,
    PHASE_STATE_FILE,
    WORKSPACE_DIR,
    path_escapes_workspace,
    write_json_atomic,
    ERR_MISSING_ARTIFACTS,
    ERR_REVIEWER_CONTRACT_FAILURE,
    ERR_VISUAL_UNVERIFIED,
    ERR_BEHAVIORAL_UNVERIFIED,
    ERR_REGRESSION_UNVERIFIED,
    ERR_REGRESSION_PRIOR_PHASE,
    ERR_VALIDATION_FAILED,
)

# Phases that produce user-visible output and therefore require a screenshot
# artifact + a reviewer visual_verification verdict. Identified by roadmap
# subsystem prefix. Convention used across Lullabeast roadmaps:
#   UI-*   — surfaces visible to the end user (rendered UI, styling, themes)
#   INT-*  — final integration: full system end-to-end (always rendered if UI exists)
#
# Operators with project-specific phases that produce rendered output under a
# non-UI/INT prefix can extend coverage via AUTODEV_VISUAL_PHASE_RAW_IDS
# (comma-separated list of raw phase IDs, e.g. "CORE-E4,SETUP-E2"). This keeps
# the default rule generic while allowing per-project tightening without
# editing the gate.
import os as _os
_VISUAL_PHASE_PREFIXES = {"UI", "INT"}


def _extra_visual_raw_ids() -> set:
    raw = (_os.environ.get("AUTODEV_VISUAL_PHASE_RAW_IDS") or "").strip()
    if not raw:
        return set()
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def _is_visual_phase(phase_raw_id):
    """Return True if this phase produces user-visible output and requires
    a visual-verification artifact from the reviewer.

    Default: any phase whose subsystem prefix is UI or INT.
    Override: set AUTODEV_VISUAL_PHASE_RAW_IDS env var to extend the set with
    project-specific raw phase IDs.
    """
    if not phase_raw_id:
        return False
    raw = str(phase_raw_id).upper()
    if raw in _extra_visual_raw_ids():
        return True
    prefix = raw.split("-", 1)[0]
    return prefix in _VISUAL_PHASE_PREFIXES


_REQUIRED_BEHAVIORAL_EVIDENCE_KEYS = ("claim", "file_or_screenshot_or_log", "method")
_MIN_BEHAVIORAL_EVIDENCE_ANCHORS = 3


def _check_behavioral_verification(data):
    """Return a list of problems with the reviewer's ``behavioral_verification``
    object, or [] if shape is valid.

    Mirror of :func:`_check_visual_verification`. Required shape on phases
    whose ``current_phase.behavioral_verification`` is populated:

      - ``verdict``: one of ``"pass"``, ``"fail"``, ``"cannot_verify"``.
      - ``how_to_check_followed``: boolean.
      - ``evidence``: list. On ``verdict == "pass"`` it MUST contain at least
        :data:`_MIN_BEHAVIORAL_EVIDENCE_ANCHORS` entries; each entry is a dict
        with ``claim``, ``file_or_screenshot_or_log``, and ``method`` keys.
        The ``file_or_screenshot_or_log`` path must be a string and is
        workspace-bounded via a canonical (``os.path.realpath``) ``commonpath``
        check that resolves symlinks on BOTH sides — same guard pattern as the
        file_manifest validator in :mod:`executor_gate`; an in-workspace symlink
        resolving outside the workspace is rejected — and must resolve on disk.

    A ``"fail"`` or ``"cannot_verify"`` verdict is not treated as a
    gate-script-level problem here — it flows through the validation block in
    :func:`evaluate_reviewer` (as ``behavioral_rejection``). This function
    only validates the *contract shape*."""
    block = data.get("behavioral_verification")
    if not isinstance(block, dict):
        return ["behavioral_verification missing or not an object"]
    verdict = block.get("verdict")
    if verdict not in ("pass", "fail", "cannot_verify"):
        return [
            f"behavioral_verification.verdict must be pass|fail|cannot_verify, got {verdict!r}"
        ]
    if not isinstance(block.get("how_to_check_followed"), bool):
        return ["behavioral_verification.how_to_check_followed must be a boolean"]
    evidence = block.get("evidence") or []
    if verdict != "pass":
        # fail / cannot_verify: shape OK without evidence anchors; the
        # rejection signal itself is the verdict.
        return []
    if not isinstance(evidence, list) or len(evidence) < _MIN_BEHAVIORAL_EVIDENCE_ANCHORS:
        return [
            f"behavioral_verification.evidence must have at least "
            f"{_MIN_BEHAVIORAL_EVIDENCE_ANCHORS} entries when verdict='pass'"
        ]
    for i, entry in enumerate(evidence):
        if not isinstance(entry, dict):
            return [f"behavioral_verification.evidence[{i}] must be an object"]
        for key in _REQUIRED_BEHAVIORAL_EVIDENCE_KEYS:
            if not entry.get(key):
                return [
                    f"behavioral_verification.evidence[{i}] missing required key {key!r}"
                ]
        path = entry["file_or_screenshot_or_log"]
        # The key-presence loop above only checks truthiness; reject a truthy
        # non-string path first, since path_escapes_workspace treats a non-str as
        # non-escaping (the validator owns the "must be a string" contract error).
        if not isinstance(path, str):
            return [
                f"behavioral_verification.evidence[{i}] file_or_screenshot_or_log must be a string"
            ]
        # Canonical (realpath) workspace-boundary check via the shared helper
        # (utils.path_escapes_workspace) — resolves symlinks on both sides.
        if path_escapes_workspace(path):
            return [
                f"behavioral_verification.evidence[{i}] path escapes workspace: {path}"
            ]
        real_path = os.path.realpath(
            path if os.path.isabs(path) else os.path.join(WORKSPACE_DIR, path)
        )
        if not os.path.exists(real_path):
            return [
                f"behavioral_verification.evidence[{i}] path does not exist on disk: {path}"
            ]
    return []


def _check_regression_verification(data, current_phase):
    """Return a list of problems with the reviewer's ``regression_verification``
    object on a phase that requires it (resolver populated both prior-phase
    fields), or [] when the shape is valid.

    Required shape:

      - ``regression_verification``: dict.
      - ``verdict``: one of ``"pass"``, ``"fail"``, ``"cannot_verify"``.
      - ``prior_phase_raw_id``: must equal ``current_phase.prior_phase_raw_id``
        (reviewer cannot claim a regression against a different prior phase).
      - ``prior_phase_how_to_check_followed``: boolean.
      - ``evidence``: list. On ``verdict == "pass"`` AND
        ``prior_phase_how_to_check_followed is True`` it MUST contain at least
        :data:`_MIN_BEHAVIORAL_EVIDENCE_ANCHORS` entries (each a dict with
        ``claim``, ``file_or_screenshot_or_log``, ``method`` keys; paths must be
        strings, are canonically (``os.path.realpath``) workspace-bounded on both
        sides, and must resolve on disk).

    A ``"fail"`` / ``"cannot_verify"`` verdict OR a ``followed: False`` signal
    is not a shape failure — those flow through the validation block as
    ``regression_rejection``. This function only validates the *contract shape*.

    Mirror of :func:`_check_behavioral_verification` — the regression evidence
    contract reuses the same anchor-quality bar.
    """
    # Deliberate coupling: changing the behavioural evidence shape changes the
    # regression evidence shape too. Both anchors share the same anchor-quality
    # bar by design.
    block = data.get("regression_verification")
    if not isinstance(block, dict):
        return ["regression_verification missing or not an object"]
    verdict = block.get("verdict")
    if verdict not in ("pass", "fail", "cannot_verify"):
        return [
            f"regression_verification.verdict must be pass|fail|cannot_verify, got {verdict!r}"
        ]
    expected_prior = current_phase.get("prior_phase_raw_id")
    actual_prior = block.get("prior_phase_raw_id")
    if actual_prior != expected_prior:
        return [
            f"regression_verification.prior_phase_raw_id must be "
            f"{expected_prior!r}, got {actual_prior!r}"
        ]
    if not isinstance(block.get("prior_phase_how_to_check_followed"), bool):
        return ["regression_verification.prior_phase_how_to_check_followed must be a boolean"]
    followed = block["prior_phase_how_to_check_followed"]
    evidence = block.get("evidence") or []
    if verdict != "pass" or not followed:
        # fail / cannot_verify / not-followed: shape OK without evidence
        # anchors; the rejection signal itself is verdict or followed.
        return []
    if not isinstance(evidence, list) or len(evidence) < _MIN_BEHAVIORAL_EVIDENCE_ANCHORS:
        return [
            f"regression_verification.evidence must have at least "
            f"{_MIN_BEHAVIORAL_EVIDENCE_ANCHORS} entries when "
            f"verdict='pass' and prior_phase_how_to_check_followed=True"
        ]
    for i, entry in enumerate(evidence):
        if not isinstance(entry, dict):
            return [f"regression_verification.evidence[{i}] must be an object"]
        for key in _REQUIRED_BEHAVIORAL_EVIDENCE_KEYS:
            if not entry.get(key):
                return [
                    f"regression_verification.evidence[{i}] missing required key {key!r}"
                ]
        path = entry["file_or_screenshot_or_log"]
        # Reject a truthy non-string path first (path_escapes_workspace treats a
        # non-str as non-escaping; the validator owns the "must be a string" contract).
        if not isinstance(path, str):
            return [
                f"regression_verification.evidence[{i}] file_or_screenshot_or_log must be a string"
            ]
        # Canonical (realpath) workspace-boundary check via the shared helper.
        if path_escapes_workspace(path):
            return [
                f"regression_verification.evidence[{i}] path escapes workspace: {path}"
            ]
        real_path = os.path.realpath(
            path if os.path.isabs(path) else os.path.join(WORKSPACE_DIR, path)
        )
        if not os.path.exists(real_path):
            return [
                f"regression_verification.evidence[{i}] path does not exist on disk: {path}"
            ]
    return []


def _synthesize_behavioral_blocking_issues(data):
    """When the reviewer recorded a behavioural failure verdict but did not
    populate ``blocking_issues``, synthesise one entry per evidence claim so
    the executor's self-heal feedback context (next reviewer-rejection retry)
    is never empty.

    Mutates ``data`` in place. Idempotent: a non-empty ``blocking_issues``
    is left untouched (the reviewer agent populated per AGENTS.md). Defensive
    fallback — primary contract is the agent populates the list; this is the
    floor that keeps the executor's targeted self-heal path armed even when
    the agent's structured output omits per-criterion entries.

    Contract: one evidence entry → one blocking issue with

      description     = claim
      attribution     = "impl"        (behavioural failures are impl failures
                                       by definition — the artifact did not
                                       exhibit the claimed behaviour)
      affected_file   = file_or_screenshot_or_log (blanked when it escapes the workspace)
      criterion_source = "behavioral"
      criterion_id    = f"behavioral_evidence[{i}]"

    The ``criterion_id`` shape mirrors a JSON-pointer-ish notation so an
    operator can `jq '.behavioral_verification.evidence[<i>]'` the source
    reviewer_output.json and retrieve the original claim. Distinct from the
    planner's ``pass_criteria[].traces_to`` anchor (which is a planning-time
    link); the two should not be conflated. See ASSUMPTIONS.md §J.
    """
    block = data.get("behavioral_verification") or {}
    if block.get("verdict") not in ("fail", "cannot_verify"):
        return
    if data.get("blocking_issues"):
        return
    evidence = block.get("evidence") or []
    if not isinstance(evidence, list) or not evidence:
        return
    synthesised = []
    for i, entry in enumerate(evidence):
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("file_or_screenshot_or_log", "")
        # Boundary-check the evidence path even on failure verdicts — the
        # pass-verdict validator (_check_behavioral_verification) was skipped via
        # its early return, so this is the only guard. An escaping path is blanked
        # (the blocking issue's claim is preserved so the executor still gets the
        # self-heal signal); the unsafe path is not propagated into
        # failure_context.json. Boundary only: a safe-but-absent path is kept,
        # because a failed phase may legitimately have produced no artifact.
        affected_file = "" if path_escapes_workspace(raw_path) else raw_path
        synthesised.append({
            "description": entry.get("claim", ""),
            "attribution": "impl",
            "affected_file": affected_file,
            "criterion_source": "behavioral",
            "criterion_id": f"behavioral_evidence[{i}]",
        })
    data["blocking_issues"] = synthesised


def _synthesize_regression_blocking_issue(data, current_phase):
    """When the reviewer's regression check rejected (verdict ∈ fail /
    cannot_verify OR ``prior_phase_how_to_check_followed: False``), append a
    SINGLE blocking_issue describing the regression so the executor's
    self-heal context names it explicitly.

    Mutates ``data`` in place. Idempotent: skip when any existing
    ``blocking_issues`` entry already carries
    ``criterion_source == "regression_prior_phase"``. This is stricter than
    the behavioural synthesiser's "blocking_issues empty" check on purpose —
    behavioural + regression coexistence requires regression to fire even
    when the behavioural synthesiser has just populated the list.

    Description selection (in order of preference):

      - ``regression_verification.failure_summary`` when present.
      - ``"Prior phase {raw_id} how_to_check recipe was not executed"`` when
        ``prior_phase_how_to_check_followed is False`` (no recipe run).
      - ``"Prior phase regression check could not verify"`` when
        ``verdict == "cannot_verify"``.
      - ``"Prior phase {raw_id} how_to_check regressed"`` on ``verdict == "fail"``.

    Entry shape:

      attribution      = "impl"
      affected_file    = evidence[0].file_or_screenshot_or_log if evidence else "" (blanked when it escapes the workspace)
      criterion_source = "regression_prior_phase"
      criterion_id     = current_phase.prior_phase_raw_id
    """
    block = data.get("regression_verification") or {}
    verdict = block.get("verdict")
    followed = block.get("prior_phase_how_to_check_followed")
    rejected = verdict in ("fail", "cannot_verify") or followed is False
    if not rejected:
        return
    existing = data.get("blocking_issues") or []
    if any(
        isinstance(bi, dict)
        and bi.get("criterion_source") == "regression_prior_phase"
        for bi in existing
    ):
        return  # idempotent — already populated

    prior_raw_id = current_phase.get("prior_phase_raw_id") or ""
    summary = block.get("failure_summary")
    if summary:
        description = str(summary)
    elif followed is False:
        description = f"Prior phase {prior_raw_id} how_to_check recipe was not executed"
    elif verdict == "cannot_verify":
        description = "Prior phase regression check could not verify"
    else:
        description = f"Prior phase {prior_raw_id} how_to_check regressed"

    evidence = block.get("evidence") or []
    first_path = ""
    if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
        candidate = evidence[0].get("file_or_screenshot_or_log") or ""
        # Same boundary guard as the behavioural synthesiser: blank an escaping
        # path (the validator's check was skipped on this non-pass verdict),
        # keep the regression blocking issue's description.
        first_path = "" if path_escapes_workspace(candidate) else candidate

    new_entry = {
        "description": description,
        "attribution": "impl",
        "affected_file": first_path,
        "criterion_source": "regression_prior_phase",
        "criterion_id": prior_raw_id,
    }
    # Append rather than overwrite so a behavioural-synthesised list survives.
    data["blocking_issues"] = list(existing) + [new_entry]


def _load_current_phase():
    """Return the full ``current_phase.json`` dict, or {} on miss.

    A sibling of :func:`_get_current_phase_raw_id` that returns the full
    payload instead of just the raw_id. Used by
    :func:`_requires_behavioral_verification` and the
    ``behavioral_rejection`` branch in :func:`evaluate_reviewer`."""
    current_phase_path = os.path.join(ARTIFACTS_DIR, "current_phase.json")
    if not os.path.exists(current_phase_path):
        return {}
    try:
        with open(current_phase_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _check_visual_verification(data):
    """Return a list of problems with the reviewer's visual_verification +
    visual_smoke_artifacts fields, or [] if all present and valid.

    Required shape on visual phases:
      - visual_verification: one of "pass", "fail", "cannot_verify"
      - visual_smoke_artifacts: list with ≥1 entry when verification == "pass".
        Each entry must be a dict with a `path` key that resolves on disk and,
        after symlink resolution (``realpath``), stays inside the workspace —
        the same ``commonpath`` boundary check behavioral/regression evidence
        enforce (T7.4; a path escaping the workspace is rejected, not followed).

    A "fail" or "cannot_verify" verdict is not treated as a gate-script-level
    problem here — it flows through the existing blocking_issues path in
    evaluate_reviewer. This function only validates the *contract shape*.

    See :func:`_check_behavioral_verification` for the parallel content-driven
    contract that validates the universal P0 behavioural-verification block."""
    verdict = data.get("visual_verification")
    if verdict not in ("pass", "fail", "cannot_verify"):
        return [
            f"visual_verification must be one of pass|fail|cannot_verify, got {verdict!r}"
        ]

    artifacts = data.get("visual_smoke_artifacts") or []
    if verdict == "pass":
        if not isinstance(artifacts, list) or len(artifacts) == 0:
            return ["visual_smoke_artifacts must be a non-empty list when visual_verification='pass'"]
        for i, entry in enumerate(artifacts):
            if not isinstance(entry, dict):
                return [f"visual_smoke_artifacts[{i}] must be an object"]
            path = entry.get("path")
            if not path or not isinstance(path, str):
                return [f"visual_smoke_artifacts[{i}] missing path"]
            # Canonical (realpath) workspace-boundary check via the shared helper,
            # parity with behavioral/regression evidence — resolves symlinks on
            # both sides (CLAUDE.md Security Constraints; do not weaken).
            if path_escapes_workspace(path):
                return [f"visual_smoke_artifacts[{i}] path escapes workspace: {path}"]
            real_path = os.path.realpath(
                path if os.path.isabs(path) else os.path.join(WORKSPACE_DIR, path)
            )
            if not os.path.exists(real_path):
                return [f"visual_smoke_artifacts[{i}] path does not exist on disk: {path}"]
    return []


def evaluate_reviewer(output_path=None):
    if output_path is None:
        output_path = os.path.join(ARTIFACTS_DIR, "reviewer_output.json")

    # ------------------------------------------------------------------
    # FIND-DONE-CRITERIA: Deterministic pre-review artifact compliance check.
    # Runs BEFORE the reviewer output is evaluated — if mandatory completion
    # artifacts are missing the executor is re-invoked with a specific
    # instruction to produce them.  This does NOT consume reviewer_retries.
    # ------------------------------------------------------------------
    _current_phase_raw_id = _get_current_phase_raw_id()
    if _current_phase_raw_id:
        _missing = _check_done_criteria_artifacts(_current_phase_raw_id)
        if _missing:
            record_error_code_only("reviewer", ERR_MISSING_ARTIFACTS)
            print(
                f"[GATE] MISSING_ARTIFACTS: {_missing}",
                file=sys.stderr,
            )
            return "MISSING_ARTIFACTS"

    data = load_json_safe(output_path, "reviewer")
    if data is None:
        # The reviewer session ended without a parseable reviewer_output.json
        # (missing or malformed). This is a CONTRACT failure, NOT a code-quality
        # rejection: the reviewer never produced a usable verdict, so reviewer_retries
        # is untouched. It is also NOT an "infrastructure" outage — genuine
        # transport/provider failures are peeled off upstream by the orchestrator
        # (stall detection, dead-on-arrival, provider-rejected) before this verdict is
        # consumed, and the plugin's agent_end backstop writes the .done sentinel
        # unconditionally, so the session may have given up cleanly OR been
        # aborted/crashed. Either way the reviewer breached its output contract.
        # FIND-REVIEWER-CONTRACT: distinct from the valid-rejection path.
        record_error_code_only("reviewer", ERR_REVIEWER_CONTRACT_FAILURE)
        return "CONTRACT_FAILURE"

    # ------------------------------------------------------------------
    # FIND-VISUAL-VERIFICATION: On phases that produce user-visible output,
    # the reviewer must include a visual_verification verdict and the
    # screenshot artifact(s) they inspected. Missing or malformed → re-invoke
    # the reviewer with a specific instruction. Does NOT consume
    # reviewer_retries (this is a contract-shape failure, not a code-quality
    # rejection — the reviewer never produced the visual judgment we need).
    # ------------------------------------------------------------------
    if _is_visual_phase(_current_phase_raw_id):
        visual_problems = _check_visual_verification(data)
        if visual_problems:
            record_error_code_only(
                "reviewer", ERR_VISUAL_UNVERIFIED,
                detail=visual_problems, detail_field="reviewer_unverified_detail",
            )
            print(
                f"[GATE] VISUAL_UNVERIFIED ({_current_phase_raw_id}): {visual_problems}",
                file=sys.stderr,
            )
            return "VISUAL_UNVERIFIED"

    # ------------------------------------------------------------------
    # FIND-BEHAVIORAL-VERIFICATION: P0 Stage F. Any phase whose
    # current_phase.json carries a populated behavioral_verification block
    # requires a structured ``behavioral_verification`` object on the
    # reviewer output. Content-driven (effectively universal under P0).
    # Missing or malformed → re-invoke the reviewer (NON-retry-consuming,
    # mirrors VISUAL_UNVERIFIED — the reviewer never produced the
    # judgment we need, so a "code-quality" retry should not be burned).
    # ------------------------------------------------------------------
    _current_phase = _load_current_phase()
    if phase_has_behavioral_block(_current_phase):
        behavioral_problems = _check_behavioral_verification(data)
        if behavioral_problems:
            record_error_code_only(
                "reviewer", ERR_BEHAVIORAL_UNVERIFIED,
                detail=behavioral_problems, detail_field="reviewer_unverified_detail",
            )
            print(
                f"[GATE] BEHAVIORAL_UNVERIFIED ({_current_phase_raw_id}): {behavioral_problems}",
                file=sys.stderr,
            )
            return "BEHAVIORAL_UNVERIFIED"

    # ------------------------------------------------------------------
    # FIND-REGRESSION-VERIFICATION: P1 Stage D. Phases whose
    # current_phase.json carries a prior_phase_raw_id AND a
    # prior_phase_how_to_check (resolver populated when the most recent
    # completed phase had a behavioural recipe) require a structured
    # ``regression_verification`` object on the reviewer output. Missing
    # or malformed → re-invoke the reviewer (NON-retry-consuming on the
    # pooled ``reviewer_unverified_retries`` counter — orchestrator-side).
    # Mirror of BEHAVIORAL_UNVERIFIED. N→N-1 only; promotion to full
    # iteration is P3 Stage B.
    # ------------------------------------------------------------------
    if requires_regression_verification(_current_phase):
        regression_problems = _check_regression_verification(data, _current_phase)
        if regression_problems:
            record_error_code_only(
                "reviewer", ERR_REGRESSION_UNVERIFIED,
                detail=regression_problems, detail_field="reviewer_unverified_detail",
            )
            print(
                f"[GATE] REGRESSION_UNVERIFIED ({_current_phase_raw_id}): {regression_problems}",
                file=sys.stderr,
            )
            return "REGRESSION_UNVERIFIED"

    blocking_issues = data.get("blocking_issues")
    if blocking_issues is None:
        blocking_issues = []

    # A visual_verification of "fail" or "cannot_verify" is itself a blocking
    # issue even if the reviewer didn't add an entry to blocking_issues.
    visual_verdict = data.get("visual_verification")
    visual_rejection = (
        _is_visual_phase(_current_phase_raw_id)
        and visual_verdict in ("fail", "cannot_verify")
    )

    # P0 Stage F: a behavioral_verification verdict of "fail" or
    # "cannot_verify" on a behavioural phase is a code-quality rejection
    # (legitimately consumes a reviewer_retries slot). This replaces the
    # ``not data.get("phase_intent_validated")`` trigger that lived here
    # before — the boolean was self-attested and unverifiable; the
    # structured verdict is anchored to evidence.
    # T1.2 — ``or {}`` only rescues a falsy value; a truthy non-dict
    # behavioral_verification crashes ``.get``. On a behavioural phase a non-dict
    # block was already intercepted upstream (BEHAVIORAL_UNVERIFIED); this line is
    # reachable with a non-dict only on a NON-behavioural phase, where the verdict
    # is irrelevant — so treat a non-dict as absent (None) and ignore it.
    _bv = data.get("behavioral_verification")
    behavioral_verdict = _bv.get("verdict") if isinstance(_bv, dict) else None
    behavioral_rejection = (
        phase_has_behavioral_block(_current_phase)
        and behavioral_verdict in ("fail", "cannot_verify")
    )

    # P1 Stage D: a regression_verification verdict of "fail" or
    # "cannot_verify", OR ``prior_phase_how_to_check_followed: False``, is
    # a code-quality rejection on the same lifetime budget as any other
    # reviewer-driven executor rejection (per the locked rejection-budget
    # decision — no per-flavour rejection counter). Routes through
    # ROUTE_EXECUTOR via the existing apply_reviewer_routing pass logic.
    # T1.2 — normalize a non-dict regression_verification to {} explicitly.
    # Defense-in-depth: the ``.get`` calls below are short-circuited behind
    # requires_regression_verification and a non-dict block is already intercepted
    # upstream as REGRESSION_UNVERIFIED, so this is not independently crash-
    # reachable today — but it removes the reliance on that subtle invariant.
    _rb = data.get("regression_verification")
    _regression_block = _rb if isinstance(_rb, dict) else {}
    regression_rejection = requires_regression_verification(_current_phase) and (
        _regression_block.get("verdict") in ("fail", "cannot_verify")
        or _regression_block.get("prior_phase_how_to_check_followed") is False
    )

    if (len(blocking_issues) > 0 or
        not data.get("integration_tests_passing") or
        visual_rejection or
        behavioral_rejection or
        regression_rejection):

        # P1 Stage D: emit ERR_REGRESSION_PRIOR_PHASE when regression is
        # the sole failing dimension so operators can grep one error code
        # for "demo viewer broke an old feature." Any other coexisting
        # rejection falls back to the generic ERR_VALIDATION_FAILED — a
        # mixed failure is not a regression failure specifically.
        _only_regression = (
            regression_rejection
            and not behavioral_rejection
            and not visual_rejection
            and not blocking_issues
            and data.get("integration_tests_passing")
        )
        if _only_regression:
            record_error_code_only("reviewer", ERR_REGRESSION_PRIOR_PHASE)
        else:
            record_error_code_only("reviewer", ERR_VALIDATION_FAILED)

        # P0 Stage G: synthesise per-evidence-entry blocking_issues when the
        # reviewer recorded a behavioural failure with an empty list.
        # P1 Stage D: independently synthesise a single regression
        # blocking_issue when the regression check rejected (regression
        # synthesiser idempotency keys on criterion_source so it coexists
        # with the behavioural synthesiser — dual-failure case).
        # Persist any augmented payload back to reviewer_output.json
        # (atomic mkstemp + os.replace) so the orchestrator's downstream
        # read sees the canonical list. apply_reviewer_routing stays pure
        # routing.
        _mutated = False
        if behavioral_rejection:
            _synthesize_behavioral_blocking_issues(data)
            _mutated = True
        if regression_rejection:
            _synthesize_regression_blocking_issue(data, _current_phase)
            _mutated = True
        if _mutated:
            try:
                write_json_atomic(output_path, data, indent=2)
            except Exception as _e:
                print(f"[GATE] synthesise write-back failed: {_e}", file=sys.stderr)

        return apply_reviewer_routing(data)

    return "PASS"


def _get_current_phase_raw_id() -> str:
    """Return current_phase_raw_id from current_phase.json, or empty string."""
    current_phase_path = os.path.join(ARTIFACTS_DIR, "current_phase.json")
    if not os.path.exists(current_phase_path):
        return ""
    try:
        with open(current_phase_path, "r") as f:
            data = json.load(f)
        return data.get("raw_id", "")
    except Exception:
        return ""


def _check_done_criteria_artifacts(phase_raw_id: str) -> list:
    """Return a list of missing artifact descriptions, or empty list if all present.

    Checks:
    1. phases/{phase_raw_id}.md exists in the project root (WORKSPACE_DIR).
    2. metrics.jsonl exists in the project root AND its last non-empty line
       contains the current phase_raw_id.
    """
    missing = []

    # Check 1: phase archive
    phase_archive_path = os.path.join(ARTIFACTS_DIR, "phases", f"{phase_raw_id}.md")
    if not os.path.exists(phase_archive_path):
        missing.append(f"phases/{phase_raw_id}.md")

    # Check 2: metrics.jsonl with current phase entry
    metrics_path = os.path.join(ARTIFACTS_DIR, "metrics.jsonl")
    if not os.path.exists(metrics_path):
        missing.append("metrics.jsonl (file missing)")
    else:
        try:
            last_line = ""
            with open(metrics_path, "r") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        last_line = stripped
            if phase_raw_id not in last_line:
                missing.append(f"metrics.jsonl (last line does not contain {phase_raw_id!r})")
        except Exception:
            missing.append("metrics.jsonl (unreadable)")

    return missing


def apply_reviewer_routing(data):
    """Pass 1: executor, Pass 2: any-plan pivot, Pass 3: escalate.

    Pass-2 routing (P0 Stage H — folded-in Stage G callout #1): if ANY
    blocking_issue carries ``attribution: "plan"``, route to planner.
    Otherwise route to executor. The legacy pivot only inspected
    ``blocking_issues[0].attribution``, making the routing decision
    ordering-sensitive — a valid plan-attributed issue at index 1+ would
    silently route to executor and the planner-spec problem would never
    get fixed.

    Defensive ``isinstance(bi, dict)`` coalesce guards against pathological
    non-dict entries (the legacy fixture in
    ``test_route_executor_writes_failure_context_atomically`` passes
    string-shaped issues; this pivot survives them by treating non-dict
    entries as carrying no attribution).
    """
    state_data = {}
    if os.path.exists(PHASE_STATE_FILE):
        try:
            with open(PHASE_STATE_FILE, 'r') as f:
                state_data = json.load(f)
        except Exception:
            pass

    retries = state_data.get("reviewer_retries", 0)
    pass_number = retries + 1

    if pass_number == 1:
        return "ROUTE_EXECUTOR"
    elif pass_number == 2:
        issues = data.get("blocking_issues") if data else None
        if issues:
            any_plan = any(
                (bi if isinstance(bi, dict) else {}).get("attribution") == "plan"
                for bi in issues
            )
            return "ROUTE_PLANNER" if any_plan else "ROUTE_EXECUTOR"
        return "ROUTE_EXECUTOR"  # fallback
    else:
        return "ROUTE_ESCALATE"

if __name__ == "__main__":
    result = evaluate_reviewer(sys.argv[1] if len(sys.argv) > 1 else None)
    print(result)
