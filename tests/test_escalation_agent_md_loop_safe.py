"""Escalation/AGENTS.md doc-contract tests — loop-safe diagnostic reads.

Pins the read-contract the escalation agent must follow to NOT thrash on
missing/moved diagnostic files. The live failure (session 32f8ca8d): the agent
issued 222 ``read`` calls — almost all ENOENT — because its doc pointed reads at
the volatile ``pipeline-project/`` workspace symlink (repointed mid-turn by a
queue auto-advance) and told it to "read all available context", with no
read-once / proceed-on-missing guard. A looping escalation agent pins the single
GPU and cascades into the reviewer failures.

These tests assert the doc instructs (1) reading diagnostics at the absolute
path the orchestrator emits in the invocation message (symlink-move-immune),
(2) ``phase_state.json`` as the always-present required file with the output
JSONs optional, and (3) explicit read-once / do-not-retry-missing / never-loop
behaviour. The agent's runtime behaviour is driven by this doc — silent drift
re-opens the loop.
"""

import os

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
ESCALATION_AGENTS_MD = os.path.join(
    REPO_ROOT, "autodev", "agents", "escalation", "AGENTS.md"
)


@pytest.fixture(scope="module")
def agents_md_text():
    with open(ESCALATION_AGENTS_MD, "r", encoding="utf-8") as f:
        return f.read()


def test_diagnostic_reads_use_absolute_invocation_path(agents_md_text):
    """Diagnostic READS must be pointed at the absolute project-artifacts path
    the orchestrator emits in the invocation message — NOT the volatile
    ``pipeline-project/`` workspace symlink, which a queue advance can repoint
    mid-turn (the ENOENT→loop trigger). Catches a regression back to
    symlink-relative diagnostic reads.

    (The WRITE of escalation_summary.json still legitimately goes through the
    workspace symlink — that is unchanged and not what this asserts.)
    """
    lowered = agents_md_text.lower()
    assert "absolute" in lowered and "invocation message" in lowered, (
        "escalation/AGENTS.md must tell the agent to read diagnostics from the "
        "ABSOLUTE path given in its invocation message (symlink-move-immune), "
        "not the volatile pipeline-project/ workspace symlink"
    )


def test_phase_state_is_required_always_present(agents_md_text):
    """``phase_state.json`` is the one diagnostic guaranteed to exist at
    escalation time (it carries ``escalation_trigger_reason``). The doc must
    mark it as the always-present / required source so the agent can always
    produce a summary from it alone — without it, the agent treats every read
    as equally-optional and loops hunting for richer files."""
    # Find phase_state.json and require 'always present' or 'required' nearby.
    idx = agents_md_text.find("phase_state.json")
    assert idx != -1, "escalation/AGENTS.md must reference phase_state.json"
    found = False
    start = 0
    while True:
        i = agents_md_text.find("phase_state.json", start)
        if i == -1:
            break
        window = agents_md_text[max(0, i - 200): i + 200].lower()
        if "always present" in window or "always-present" in window or "required" in window:
            found = True
            break
        start = i + 1
    assert found, (
        "escalation/AGENTS.md must mark phase_state.json as the always-present / "
        "required diagnostic near where it is listed"
    )


def test_output_jsons_marked_optional(agents_md_text):
    """planner/executor/reviewer_output.json are routinely absent at escalation
    time (e.g. the reviewer's verdict-write died — the #3 bug). The doc must
    mark them OPTIONAL so the agent does not block/loop waiting for them."""
    lowered = agents_md_text.lower()
    assert "optional" in lowered, (
        "escalation/AGENTS.md must explicitly mark the *_output.json diagnostics "
        "as optional (they are frequently absent at escalation time)"
    )


def test_anti_loop_read_once_proceed_on_missing(agents_md_text):
    """The core loop guard: read each file AT MOST ONCE; on ENOENT do NOT retry
    / re-read; proceed to write the summary from what was read. This is the
    instruction whose absence let the local model issue 222 reads. Catches the
    guard being weakened or dropped."""
    lowered = agents_md_text.lower()
    assert "at most once" in lowered or "read each" in lowered and "once" in lowered, (
        "escalation/AGENTS.md must instruct reading each diagnostic at most once"
    )
    assert "do not retry" in lowered or "do not re-read" in lowered, (
        "escalation/AGENTS.md must instruct NOT to retry / re-read a missing file"
    )
    assert "never loop" in lowered or "do not loop" in lowered, (
        "escalation/AGENTS.md must explicitly forbid looping on missing reads"
    )
