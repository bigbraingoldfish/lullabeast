"""P1 Stage F — Python reachability resolver.

The Python resolver performs a static AST walk from an entry script, following
``import`` / ``from ... import`` statements through the project source. It
deliberately skips stdlib + third-party (anything not under ``project_root``)
and emits limitations for dynamic imports (``importlib``) and syntax errors.

These tests pin the resolver's contract independently of the executor gate;
the gate-level integration is tested in
``test_executor_gate_reachability_advisory.py``.
"""

import os

import pytest

# conftest.py wires GATE_SCRIPTS_DIR into sys.path so `reachability` resolves
# as a sibling of ``utils``.
from reachability import PythonResolver  # noqa: E402


def _write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)


def _norm(rel_set, project_root):
    return {os.path.normpath(p) for p in rel_set}


def test_simple_import_chain(tmp_path):
    """entry imports A, A imports B → all three reachable, no limitations."""
    root = str(tmp_path)
    _write(os.path.join(root, "main.py"), "from a import x\n")
    _write(os.path.join(root, "a.py"), "from b import y\n")
    _write(os.path.join(root, "b.py"), "")
    result = PythonResolver().resolve(root, "python main.py")
    assert _norm(result.reachable, root) == {"main.py", "a.py", "b.py"}
    assert result.limitations == []
    assert result.entry_resolved == "main.py"


def test_unreachable_module_flagged(tmp_path):
    """entry → A only; B and dead.py exist in the workspace but no import
    chain reaches them. The resolver returns only the reachable set; the
    gate-helper (separately tested) compares against the manifest."""
    root = str(tmp_path)
    _write(os.path.join(root, "main.py"), "from a import x\n")
    _write(os.path.join(root, "a.py"), "")
    _write(os.path.join(root, "dead.py"), "print('nothing imports me')\n")
    result = PythonResolver().resolve(root, "python main.py")
    reachable = _norm(result.reachable, root)
    assert "dead.py" not in reachable
    assert "main.py" in reachable and "a.py" in reachable


def test_dynamic_importlib_treated_as_warning_only(tmp_path):
    """importlib.import_module is not statically resolvable — the resolver
    must NOT follow the dynamic import (so the target won't be marked
    reachable), but it MUST emit a limitation so the gate-helper can surface
    the partial-coverage signal."""
    root = str(tmp_path)
    _write(
        os.path.join(root, "main.py"),
        "import importlib\nmod = importlib.import_module('a')\n",
    )
    _write(os.path.join(root, "a.py"), "")
    result = PythonResolver().resolve(root, "python main.py")
    reachable = _norm(result.reachable, root)
    assert "a.py" not in reachable, (
        "static walk must NOT follow importlib.import_module — that's the "
        "whole reason limitations exist"
    )
    assert any("importlib" in lim for lim in result.limitations)


def test_stdlib_imports_skipped_silently(tmp_path):
    """Bare stdlib imports (os, sys, json) must not produce limitations —
    that would generate noise on every realistic Python entry script."""
    root = str(tmp_path)
    _write(os.path.join(root, "main.py"), "import os\nimport sys\nimport json\n")
    result = PythonResolver().resolve(root, "python main.py")
    assert _norm(result.reachable, root) == {"main.py"}
    assert result.limitations == [], (
        f"stdlib imports must skip silently; got limitations: {result.limitations!r}"
    )


def test_syntax_error_in_dependency_does_not_crash(tmp_path):
    """Unparseable dependency files must NOT bubble up an exception — the
    helper must catch SyntaxError, emit a limitation naming the file, and
    continue. main.py reached `a.py`, so `a.py` stays in reachable."""
    root = str(tmp_path)
    _write(os.path.join(root, "main.py"), "from a import x\n")
    _write(os.path.join(root, "a.py"), "def(:")  # malformed
    result = PythonResolver().resolve(root, "python main.py")
    reachable = _norm(result.reachable, root)
    assert "a.py" in reachable
    assert any("a.py" in lim and "parse" in lim.lower() for lim in result.limitations)


def test_uvicorn_entry_derivation(tmp_path):
    """`uvicorn pkg.mod:app` derives the entry from the dotted module name."""
    root = str(tmp_path)
    _write(os.path.join(root, "pkg", "__init__.py"), "")
    _write(os.path.join(root, "pkg", "mod.py"), "from a import x\napp = None\n")
    _write(os.path.join(root, "a.py"), "")
    result = PythonResolver().resolve(root, "uvicorn pkg.mod:app --port 8000")
    reachable = _norm(result.reachable, root)
    assert os.path.normpath("pkg/mod.py") in reachable
    assert "a.py" in reachable


def test_python_dash_m_entry_derivation(tmp_path):
    """`python -m pkg.mod` derives the entry the same way uvicorn does."""
    root = str(tmp_path)
    _write(os.path.join(root, "pkg", "__init__.py"), "")
    _write(os.path.join(root, "pkg", "mod.py"), "from a import x\n")
    _write(os.path.join(root, "a.py"), "")
    result = PythonResolver().resolve(root, "python -m pkg.mod")
    reachable = _norm(result.reachable, root)
    assert os.path.normpath("pkg/mod.py") in reachable
    assert "a.py" in reachable


def test_missing_entry_file_returns_no_entry_resolved(tmp_path):
    """If the entry script does not exist on disk, the resolver returns
    entry_resolved=None and an empty reachable set. The gate helper then
    emits a resolver_error diagnostic — the resolver itself does not raise."""
    root = str(tmp_path)
    # No main.py written.
    result = PythonResolver().resolve(root, "python main.py")
    assert result.entry_resolved is None
    assert result.reachable == set()
