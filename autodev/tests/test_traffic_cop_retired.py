"""Traffic-cop retirement guards (source-level).

The reviewer "model health → SSH recovery" machinery and the executor→reviewer
``wait_for_model_stable()`` model-swap wait were retired. The pipeline runs all
agents on cloud providers (only the escalation agent is local), and agent/model
liveness is owned by the OpenClaw activity-stamp hooks (startup-grace / stall
detection in ``poll_for_sentinel``). The reviewer no-parseable-output branch
(formerly ``INFRA_FAILURE``, now ``CONTRACT_FAILURE``) collapses to an
unconditional soft-retry of the reviewer (cap ``reviewer_contract_retries``) →
escalate ``CONTRACT_FAILURE_SOFT_RETRY_EXHAUSTED``.

Background: a cloud reviewer (``openrouter/...``) emitted malformed JSON; the
old handler probed the *local* ``127.0.0.1:11434`` ``/health`` endpoint, got a
false "unhealthy", took the SSH-recovery branch with empty ``recovery`` config →
``ssh`` exit 255 → false human escalation, bypassing the self-healing
soft-retry. Removing the probe + SSH branch makes the soft-retry unconditional.

These are source-level guards: the in-``run()`` handler is not extractable
without a refactor, so the repo idiom for pinning it is source inspection
(see ``test_reviewer_routing_dispatch.py`` and ``test_polling_mechanism.py``).
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()


def _contract_failure_block() -> str:
    """Return the source slice of the reviewer ``CONTRACT_FAILURE`` handler.

    Bounded by the next reviewer-gate branch, the pooled contract-shape handler
    ``elif gate_result in (`` (VISUAL/BEHAVIORAL/REGRESSION_UNVERIFIED).
    """
    start = _ORCH_SRC.find('elif gate_result == "CONTRACT_FAILURE":')
    assert start != -1, "CONTRACT_FAILURE handler not found in orchestrator.py"
    end = _ORCH_SRC.find("elif gate_result in (", start)
    assert end != -1, "could not bound the CONTRACT_FAILURE block"
    return _ORCH_SRC[start:end]


def test_check_traffic_cop_health_removed():
    assert "def check_traffic_cop_health" not in _ORCH_SRC, (
        "check_traffic_cop_health() must be removed — the local /health probe "
        "false-negatived a cloud reviewer and routed the no-output branch into a "
        "dead-end SSH recovery (the svg-pic2/INFRA-E1 false escalation)."
    )


def test_wait_for_model_stable_removed():
    assert "def wait_for_model_stable" not in _ORCH_SRC, (
        "wait_for_model_stable() must be removed — no GPU model swap occurs "
        "between cloud agents; it only added latency / a 300s stall risk."
    )
    assert "wait_for_model_stable(" not in _ORCH_SRC, (
        "no lingering wait_for_model_stable() call sites may remain."
    )


def test_contract_failure_branch_has_no_ssh_recovery():
    block = _contract_failure_block()
    for forbidden in (
        "subprocess",
        '"recovery"',
        "check_traffic_cop_health",
        "reviewer_infra_recovery",
        "INFRA_FAILURE_RECOVERY",
        "wait_for_model_stable",
    ):
        assert forbidden not in block, (
            f"CONTRACT_FAILURE handler must not contain {forbidden!r} — the SSH "
            f"recovery / model-health branch was retired."
        )


def test_contract_failure_branch_soft_retries():
    block = _contract_failure_block()
    assert "reviewer_contract_retries" in block, (
        "CONTRACT_FAILURE must self-heal via the reviewer_contract_retries "
        "soft-retry counter."
    )
    assert "CONTRACT_FAILURE_SOFT_RETRY_EXHAUSTED" in block, (
        "CONTRACT_FAILURE must escalate with CONTRACT_FAILURE_SOFT_RETRY_EXHAUSTED "
        "at the cap."
    )


def test_executor_reviewer_handoff_no_model_wait():
    idx = _ORCH_SRC.find('"Executor passed, moving to reviewer"')
    assert idx != -1, "executor→reviewer handoff transition not found"
    window = _ORCH_SRC[idx: idx + 400]
    assert "wait_for_model_stable" not in window, (
        "executor→reviewer handoff must transition straight to the reviewer; "
        "the model-swap wait was retired."
    )
