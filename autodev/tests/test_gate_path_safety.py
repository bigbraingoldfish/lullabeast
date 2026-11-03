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


# ---------------------------------------------------------------------------
# resolve_workspace_path: every agent reaches the project through a link named
# ``pipeline-project`` in its own workspace and writes paths through it (each
# agent's TOOLS.md), while the gates resolve relative paths against the project
# folder that link points to. Both spellings must name the same file.
# ---------------------------------------------------------------------------


def test_link_prefix_names_the_same_project_file(workspace):
    """``pipeline-project/<p>`` resolves to ``<p>`` in the project, so evidence
    cited the way the reviewer's TOOLS.md writes paths is found."""
    expected = os.path.join(workspace, ".autodev/pipeline/shot.png")
    for cited in (
        ".autodev/pipeline/shot.png",
        "pipeline-project/.autodev/pipeline/shot.png",
        "./pipeline-project/.autodev/pipeline/shot.png",
        "pipeline-project//.autodev/pipeline/shot.png",
    ):
        assert utils_module.resolve_workspace_path(cited, workspace) == expected, cited


def test_link_prefix_kept_when_the_project_has_that_entry(workspace):
    """A project with its own ``pipeline-project`` entry keeps the path as
    written: the prefix is dropped only when it names nothing in the project."""
    os.mkdir(os.path.join(workspace, "pipeline-project"))
    assert utils_module.resolve_workspace_path("pipeline-project/x.txt", workspace) == (
        os.path.join(workspace, "pipeline-project/x.txt")
    )


def test_bare_link_name_and_absolute_paths_are_left_alone(workspace):
    """Only a ``pipeline-project/`` folder prefix is rewritten; a file named
    ``pipeline-project`` and absolute paths pass through unchanged."""
    assert utils_module.resolve_workspace_path("pipeline-project", workspace) == (
        os.path.join(workspace, "pipeline-project")
    )
    assert utils_module.resolve_workspace_path("/etc/passwd", workspace) == "/etc/passwd"


def test_link_prefix_cannot_hide_an_escape(workspace):
    """The boundary check runs on the resolved path: dropping the prefix never
    turns an escape into an in-bounds path, and an in-bounds one stays in."""
    assert utils_module.path_escapes_workspace("pipeline-project/../../etc/passwd") is True
    assert utils_module.path_escapes_workspace("pipeline-project/ok.txt") is False
