"""Section 6.1.d — stamp_init_failed event.

``_init_activity_stamp_or_halt`` (Section 5a) logs ``[FATAL] activity
stamp init failed ...`` and transitions to ``HALTED_SILENT`` when the
workspace dir is unwritable.  Without a structured event, the UI
shows the pipeline as silently halted with no indication of *why* —
operators must tail ``/tmp/orchestrator.log`` to discover the cause.

This test pins that the helper emits ``stamp_init_failed`` alongside
the existing FATAL print so the activity tab can render the failure
distinctly from other HALTED_SILENT causes.
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()


def test_stamp_init_failed_event_emitted_on_failure():
    """``_init_activity_stamp_or_halt`` must emit ``stamp_init_failed``
    before transitioning to HALTED_SILENT so the UI can render the
    failure distinctly."""
    method_idx = _ORCH_SRC.find("def _init_activity_stamp_or_halt")
    assert method_idx != -1, "Could not find _init_activity_stamp_or_halt method"
    next_def = _ORCH_SRC.find("\n    def ", method_idx + 1)
    method_body = _ORCH_SRC[method_idx : next_def if next_def != -1 else method_idx + 3000]
    pat = re.compile(r'_write_pipeline_event\(\s*["\']stamp_init_failed["\']')
    assert pat.search(method_body), (
        "_init_activity_stamp_or_halt must emit 'stamp_init_failed' event "
        "on the False-return path so the UI surfaces the cause of HALTED_SILENT"
    )


def test_stamp_init_failed_event_detail_includes_agent_and_stamp_path():
    """Detail must include ``agent_role`` and ``stamp_path`` — exactly
    what the operator needs to fix the underlying workspace issue."""
    method_idx = _ORCH_SRC.find("def _init_activity_stamp_or_halt")
    assert method_idx != -1
    next_def = _ORCH_SRC.find("\n    def ", method_idx + 1)
    method_body = _ORCH_SRC[method_idx : next_def if next_def != -1 else method_idx + 3000]
    for field in ("agent_role", "stamp_path"):
        pat = re.compile(
            r'_write_pipeline_event\(\s*["\']stamp_init_failed["\'][\s\S]{0,500}?'
            + field
        )
        assert pat.search(method_body), (
            f"stamp_init_failed detail must include {field!r}"
        )
