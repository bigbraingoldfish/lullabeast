"""Unit tests for ``utils.path_escapes_workspace`` — the shared workspace-
boundary helper used by the reviewer gate's contract validators and its
failure-verdict blocking-issue synthesisers.

The helper is **boundary-only** (no on-disk existence check): it answers "does
this path resolve outside the workspace?" with ``os.path.realpath`` resolution
on BOTH sides so an in-workspace symlink pointing outside is caught. These tests
pin that contract independently of any caller, so a regression in the helper is
localised here rather than surfacing as a confusing gate-behaviour failure.

Boundary-only is deliberate: the pass-verdict validators layer their own
``os.path.exists`` check on top, but the failure-verdict synthesisers must NOT
require existence (a failed phase may legitimately have produced no artifact),
so the shared helper cannot itself encode existence.
"""

import os
import tempfile
from unittest.mock import patch

import pytest

# Path wiring handled by autodev/tests/conftest.py
import utils as utils_module


@pytest.fixture
def workspace(tmp_path):
    """Patch ``utils.WORKSPACE_DIR`` to a fresh tmp dir (trailing-separator form,
    matching the module's real WORKSPACE_DIR) for the duration of a test, and
    return it. The helper reads the module global at call time, so this patch is
    all that is needed to control the boundary."""
    ws = str(tmp_path).rstrip(os.sep) + os.sep
    with patch.object(utils_module, "WORKSPACE_DIR", ws):
        yield ws


def test_safe_relative_path_does_not_escape(workspace):
    """A relative path under the workspace is in-bounds. Guards against
    over-blanking legitimate evidence paths."""
    assert utils_module.path_escapes_workspace("sub/ok.txt") is False


def test_safe_absolute_in_workspace_does_not_escape(workspace):
    """An absolute path that lands inside the workspace is in-bounds — the
    helper must handle absolute inputs, not only relative ones."""
    inside = os.path.join(workspace, "ok.txt")
    assert utils_module.path_escapes_workspace(inside) is False


def test_parent_traversal_escapes(workspace):
    """``..`` traversal that climbs out of the workspace is an escape — the core
    gap this whole change closes."""
    assert utils_module.path_escapes_workspace("../escape.txt") is True
    assert utils_module.path_escapes_workspace("../../etc/passwd") is True


def test_absolute_outside_workspace_escapes(workspace):
    """An absolute path outside the workspace is an escape."""
    assert utils_module.path_escapes_workspace("/etc/passwd") is True


def test_symlink_escape_is_caught(workspace):
    """An in-workspace symlink whose target is OUTSIDE the workspace: lexically
    inside (a non-resolved compare would pass) but ``realpath``-outside. This is
    the test that pins realpath (not lexical) resolution — the reason the helper
    resolves both sides."""
    parent = os.path.dirname(workspace.rstrip(os.sep))
    outside = tempfile.mkdtemp(dir=parent, prefix="ws_escape_")
    # Precondition: keep the test valid on symlinked-TMPDIR hosts (e.g. macOS).
    assert os.path.commonpath(
        [os.path.realpath(outside), os.path.realpath(workspace)]
    ) != os.path.realpath(workspace), "escape target must be outside the workspace"
    os.symlink(outside, os.path.join(workspace.rstrip(os.sep), "sneaky"))
    assert utils_module.path_escapes_workspace("sneaky/secret.txt") is True


def test_empty_string_does_not_escape(workspace):
    """An empty path cannot describe a traversal target; the caller keeps "" as
    "no artifact"."""
    assert utils_module.path_escapes_workspace("") is False


def test_non_string_does_not_escape(workspace):
    """A non-string value is a shape problem the caller owns, not a traversal;
    the helper must not raise on it (it is called on raw agent output)."""
    assert utils_module.path_escapes_workspace(None) is False
    assert utils_module.path_escapes_workspace(123) is False
