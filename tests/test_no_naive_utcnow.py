"""Regression guard: no deprecated naive-UTC calls in production code.

``datetime.utcnow()`` and ``datetime.utcfromtimestamp()`` are deprecated in
Python 3.12+ and emit a ``DeprecationWarning`` on every test run — repetitive
noise that kept getting re-flagged. They were all removed from ``ui/server.py``
(replaced by ``_utc_now_iso()`` / ``datetime.now(timezone.utc)``); this guard
keeps them from creeping back into production sources.

Static-lint style (mirrors ``tests/test_infra3_systemd_unit.py``): it ``ast.parse``s
each source, it does not import or run anything. Parsing (rather than a raw text
regex) means a reference inside a comment, docstring, or string literal — e.g. a
migration note that mentions ``datetime.utcfromtimestamp(...)`` — is naturally
ignored; only a real attribute access in live code is flagged.

Scope is **production code only** — ``tests/`` is deliberately excluded so the
hand-rolled mock in ``tests/test_defensive_c2_02.py`` (which legitimately defines
its own ``utcnow()`` method to patch the stdlib) is never a false positive.
"""
import ast
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The two deprecated naive-UTC constructors. ``datetime.now(timezone.utc)`` (the
# blessed replacement) is not in this set.
_FORBIDDEN_ATTRS = frozenset({"utcnow", "utcfromtimestamp"})

_BLESSED = "_utc_now_iso() (ISO-8601 'Z' string) or datetime.now(timezone.utc)"


def _production_py_files():
    """``ui/server.py`` plus every ``.py`` under ``autodev/pipeline/``."""
    files = [os.path.join(_REPO_ROOT, "ui", "server.py")]
    pipeline_root = os.path.join(_REPO_ROOT, "autodev", "pipeline")
    for dirpath, _dirs, names in os.walk(pipeline_root):
        for name in sorted(names):
            if name.endswith(".py"):
                files.append(os.path.join(dirpath, name))
    return files


def _is_forbidden_attr(node: ast.AST) -> bool:
    """True for an attribute access ``datetime.utcnow`` / ``datetime.utcfromtimestamp``.

    Matches both ``datetime.<attr>`` (``from datetime import datetime``) and
    ``datetime.datetime.<attr>`` (``import datetime``).
    """
    if not isinstance(node, ast.Attribute) or node.attr not in _FORBIDDEN_ATTRS:
        return False
    base = node.value
    if isinstance(base, ast.Name):
        return base.id == "datetime"
    if isinstance(base, ast.Attribute):
        return base.attr == "datetime"
    return False


def test_no_naive_utcnow_in_production_sources():
    """No ``datetime.utcnow()`` / ``datetime.utcfromtimestamp()`` in production code.

    Only live code is flagged — references in comments, docstrings, or string
    literals are ignored because the file is parsed, not text-scanned.
    """
    offenders = []
    for path in _production_py_files():
        with open(path, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if _is_forbidden_attr(node):
                rel = os.path.relpath(path, _REPO_ROOT)
                offenders.append(f"  {rel}:{node.lineno}: datetime.{node.attr}(...)")

    assert not offenders, (
        f"Deprecated naive-UTC call(s) found — use {_BLESSED} instead:\n"
        + "\n".join(sorted(offenders))
    )
