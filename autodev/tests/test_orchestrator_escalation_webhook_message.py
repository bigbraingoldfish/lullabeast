"""Orchestrator escalation webhook message construction must not use _PIPELINE_ARTIFACTS.

_PIPELINE_ARTIFACTS is defined only in webhook_client.py (workspace-relative path for agent
messages). The orchestrator must use PROJECT_ARTIFACTS_DIR (the host-side absolute path) when
building the escalation webhook message body.

Before the fix, orchestrator.py lines 2862 and 4132 contain `_p = _PIPELINE_ARTIFACTS`, which
raises NameError at runtime because _PIPELINE_ARTIFACTS is not defined in orchestrator's module
scope.

These tests:
  1. Fail against unpatched code (source contains _PIPELINE_ARTIFACTS references)
  2. Pass after the two lines are renamed to PROJECT_ARTIFACTS_DIR
"""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
ORCHESTRATOR_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")

for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def test_orchestrator_source_does_not_reference_pipeline_artifacts():
    """_PIPELINE_ARTIFACTS must not appear anywhere in orchestrator.py.

    This name is undefined in orchestrator's module scope — it lives in webhook_client.py
    only. Any reference to it in orchestrator.py raises NameError when the escalation
    path executes.

    FAILS before fix: orchestrator.py contains two `_p = _PIPELINE_ARTIFACTS` lines.
    PASSES after fix: both are renamed to `_p = PROJECT_ARTIFACTS_DIR`.
    """
    with open(ORCHESTRATOR_PATH, encoding="utf-8") as f:
        source = f.read()

    assert "_PIPELINE_ARTIFACTS" not in source, (
        "orchestrator.py must not reference _PIPELINE_ARTIFACTS — it is undefined in this "
        "module and will raise NameError when the escalation path executes. "
        "Use PROJECT_ARTIFACTS_DIR (the module-level constant at line 50) instead."
    )


def test_project_artifacts_dir_is_module_level_constant():
    """PROJECT_ARTIFACTS_DIR must be a module-level constant in orchestrator.

    This confirms the correct replacement for _PIPELINE_ARTIFACTS exists and is
    accessible from the escalation branches inside run().
    """
    import orchestrator as orc_module

    assert hasattr(orc_module, "PROJECT_ARTIFACTS_DIR"), (
        "PROJECT_ARTIFACTS_DIR must be defined at module level in orchestrator.py"
    )
    val = orc_module.PROJECT_ARTIFACTS_DIR
    assert ".autodev" in val and "pipeline" in val, (
        f"PROJECT_ARTIFACTS_DIR value '{val}' does not look like an .autodev/pipeline path"
    )


def test_webhook_client_pipeline_artifacts_remains_separate():
    """_PIPELINE_ARTIFACTS in webhook_client.py must remain intact.

    The rename in orchestrator.py must not affect webhook_client's own constant,
    which is the workspace-relative path string used inside agent message bodies.
    """
    webhook_client_path = os.path.join(PIPELINE_DIR, "webhook_client.py")
    with open(webhook_client_path, encoding="utf-8") as f:
        wc_source = f.read()

    assert "_PIPELINE_ARTIFACTS" in wc_source, (
        "webhook_client.py must still define _PIPELINE_ARTIFACTS — this is the "
        "workspace-relative path string sent inside agent notification messages."
    )
