"""P1 Stage F — Python static reachability resolver.

AST-based BFS from an entry script. Follows ``import`` / ``from ... import``
through workspace source. Skips stdlib + third-party silently (they're not in
the workspace, so they have no on-disk file under ``project_root``). Emits
limitations for dynamic imports (``importlib``) and syntax errors so the
gate-helper can surface partial-coverage signals.

Reachable paths are workspace-relative and ``os.path.normpath``'d.
"""

import ast
import os
import shlex

from . import ReachabilityResult


class PythonResolver:
    """Static import-graph walk from a Python entry script."""

    language = "python"

    def resolve(self, project_root, entry_point_command):
        entry_rel = self._derive_entry(project_root, entry_point_command)
        if entry_rel is None or not os.path.isfile(os.path.join(project_root, entry_rel)):
            return ReachabilityResult(reachable=set(), limitations=[], entry_resolved=None)

        reachable = {os.path.normpath(entry_rel)}
        seen = set(reachable)
        queue = [entry_rel]
        limitations = []

        while queue:
            current_rel = queue.pop(0)
            current_abs = os.path.join(project_root, current_rel)
            try:
                with open(current_abs, "r", encoding="utf-8") as f:
                    src = f.read()
            except OSError as e:
                limitations.append(f"could not read {current_rel}: {e}")
                continue
            try:
                tree = ast.parse(src, filename=current_abs)
            except SyntaxError as e:
                limitations.append(f"could not parse {current_rel}: {e.msg}")
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self._enqueue_module(
                            alias.name, current_rel, project_root,
                            reachable, seen, queue,
                        )
                elif isinstance(node, ast.ImportFrom):
                    target = self._resolve_from_import(node, current_rel)
                    if target is not None:
                        self._enqueue_module(
                            target, current_rel, project_root,
                            reachable, seen, queue,
                        )
                elif isinstance(node, ast.Call):
                    if self._is_dynamic_import_call(node):
                        limitations.append(
                            f"{current_rel}: dynamic import via importlib (static walk skipped)"
                        )

        return ReachabilityResult(
            reachable=reachable,
            limitations=limitations,
            entry_resolved=os.path.normpath(entry_rel),
        )

    # ---- entry derivation -------------------------------------------------

    def _derive_entry(self, project_root, command):
        """Best-effort entry-script derivation from the command. Returns a
        workspace-relative path or None."""
        cmd = (command or "").strip()
        if not cmd:
            return None
        try:
            tokens = shlex.split(cmd)
        except ValueError:
            tokens = cmd.split()
        if not tokens:
            return None
        head = tokens[0]
        # `python ... -m pkg.mod`
        if head.startswith("python") and "-m" in tokens:
            try:
                idx = tokens.index("-m")
                if idx + 1 < len(tokens):
                    return self._module_target_to_path(tokens[idx + 1], project_root)
            except ValueError:
                pass
        # `python path/to/foo.py [args...]`
        if head.startswith("python"):
            for tok in tokens[1:]:
                if tok.startswith("-"):
                    continue
                if tok.endswith(".py"):
                    return tok
                return None
        # `uvicorn pkg.mod:attr [args...]`, `gunicorn pkg.mod:attr ...`
        if head in ("uvicorn", "gunicorn", "hypercorn"):
            for tok in tokens[1:]:
                if tok.startswith("-"):
                    continue
                module_target = tok.split(":", 1)[0]
                return self._module_target_to_path(module_target, project_root)
        # `flask --app pkg.mod run`
        if head == "flask":
            if "--app" in tokens:
                idx = tokens.index("--app")
                if idx + 1 < len(tokens):
                    return self._module_target_to_path(tokens[idx + 1], project_root)
            return None
        return None

    def _module_target_to_path(self, dotted, project_root):
        """Try ``<project_root>/a/b/c.py`` then ``<project_root>/a/b/c/__init__.py``.
        Return the workspace-relative path or None."""
        parts = dotted.split(".")
        candidate_module = os.path.join(*parts) + ".py"
        if os.path.isfile(os.path.join(project_root, candidate_module)):
            return candidate_module
        candidate_pkg = os.path.join(*parts, "__init__.py")
        if os.path.isfile(os.path.join(project_root, candidate_pkg)):
            return candidate_pkg
        return None

    # ---- import resolution ------------------------------------------------

    def _resolve_from_import(self, node, current_rel):
        """For ``from pkg.mod import name`` / ``from .mod import name``,
        return the dotted module name resolved against ``current_rel``'s
        package."""
        if node.level == 0:
            # Absolute import: from pkg.mod import name → "pkg.mod"
            return node.module
        # Relative import: convert to absolute via current_rel's package path.
        current_pkg = os.path.dirname(current_rel).replace(os.sep, ".")
        if current_pkg:
            parts = current_pkg.split(".")
        else:
            parts = []
        # Drop `node.level - 1` directory components (level=1 → same package).
        if node.level - 1 > 0:
            parts = parts[: -(node.level - 1)] if (node.level - 1) <= len(parts) else []
        if node.module:
            parts.append(node.module)
        if not parts:
            return None
        return ".".join(parts)

    def _enqueue_module(self, dotted, current_rel, project_root, reachable, seen, queue):
        """Resolve a dotted module to a file under ``project_root`` and enqueue
        it for BFS. Silent skip when no on-disk file matches (stdlib /
        third-party / typo — we cannot distinguish without a Python interpreter
        in the loop, and the noise floor of warning on every external import
        would be intolerable)."""
        target_rel = self._module_target_to_path(dotted, project_root)
        if target_rel is None:
            return
        norm = os.path.normpath(target_rel)
        if norm in seen:
            return
        seen.add(norm)
        reachable.add(norm)
        queue.append(target_rel)

    # ---- dynamic-import detection ----------------------------------------

    def _is_dynamic_import_call(self, node):
        """Detect ``importlib.import_module(...)``, ``importlib.__import__(...)``,
        and bare ``__import__(...)`` calls — none statically resolvable."""
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in ("import_module", "__import__"):
                # Check the base is ``importlib`` (could be ``importlib`` or
                # an attribute chain ending in it).
                base = func.value
                if isinstance(base, ast.Name) and base.id == "importlib":
                    return True
                if isinstance(base, ast.Attribute) and base.attr == "importlib":
                    return True
        if isinstance(func, ast.Name) and func.id == "__import__":
            return True
        return False
