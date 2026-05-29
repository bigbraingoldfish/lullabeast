"""P1 Stage F — JS/TS static reachability resolver.

Regex-based BFS over ``import`` / ``require`` / ``export ... from`` statements.
No npm parser dependency — advisory mode tolerates regex false negatives
(misses imports in template literals or wrapped in eval-like constructs).
Reads ``package.json`` ``main`` / ``module`` / ``exports`` to derive the
entry. TS-extension-first resolution matches ``tsc --moduleResolution node``.
"""

import json
import os
import re
import shlex

from . import ReachabilityResult


_RE_IMPORT_FROM = re.compile(
    r"""(?m)^\s*import\s+(?:[^'"]*?from\s+)?['"]([^'"]+)['"]"""
)
_RE_DYNAMIC = re.compile(r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_RE_DYNAMIC_VAR = re.compile(r"""import\s*\(\s*[A-Za-z_$]""")
_RE_REQUIRE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_RE_EXPORT_FROM = re.compile(
    r"""(?m)^\s*export\s+(?:[^'"]*?from\s+)?['"]([^'"]+)['"]"""
)

# Order matters for module resolution: TS extensions first so that mixed
# projects with both .ts and .js neighbours pick the TS source.
_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_INDEX_FILES = tuple(f"index{ext}" for ext in _EXTENSIONS)


class JsTsResolver:
    """Static import/require-graph walk from a JS/TS entry script using regex
    parsing (advisory-only; misses dynamic patterns)."""

    language = "javascript"

    def resolve(self, project_root, entry_point_command):
        entry_rel, derived_from_pkg = self._derive_entry(project_root, entry_point_command)
        if entry_rel is None or not os.path.isfile(os.path.join(project_root, entry_rel)):
            return ReachabilityResult(reachable=set(), limitations=[], entry_resolved=None)

        reachable = {os.path.normpath(entry_rel)}
        if derived_from_pkg and os.path.isfile(os.path.join(project_root, "package.json")):
            reachable.add("package.json")
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

            specifiers = set()
            for pat in (_RE_IMPORT_FROM, _RE_DYNAMIC, _RE_REQUIRE, _RE_EXPORT_FROM):
                for m in pat.finditer(src):
                    specifiers.add(m.group(1))
            if _RE_DYNAMIC_VAR.search(src):
                limitations.append(
                    f"{current_rel}: dynamic import with non-literal argument (static walk skipped)"
                )

            for spec in specifiers:
                target_rel = self._resolve_specifier(spec, current_rel, project_root, limitations)
                if target_rel is None:
                    continue
                norm = os.path.normpath(target_rel)
                if norm in seen:
                    continue
                seen.add(norm)
                reachable.add(norm)
                queue.append(target_rel)

        return ReachabilityResult(
            reachable=reachable,
            limitations=limitations,
            entry_resolved=os.path.normpath(entry_rel),
        )

    # ---- entry derivation -------------------------------------------------

    def _derive_entry(self, project_root, command):
        """Return ``(rel_path, derived_from_pkg_json)``."""
        cmd = (command or "").strip()
        if not cmd:
            return (None, False)
        try:
            tokens = shlex.split(cmd)
        except ValueError:
            tokens = cmd.split()
        if not tokens:
            return (None, False)
        head = tokens[0]
        # `node path/to/foo.js`
        if head == "node":
            for tok in tokens[1:]:
                if tok.startswith("-"):
                    continue
                return (tok, False)
            return (None, False)
        # `tsx app.ts` / `ts-node script.ts`
        if head in ("tsx", "ts-node"):
            for tok in tokens[1:]:
                if tok.startswith("-"):
                    continue
                return (tok, False)
            return (None, False)
        # `vite` / `vite dev`
        if head == "vite":
            if os.path.isfile(os.path.join(project_root, "index.html")):
                return ("index.html", False)
            return (None, False)
        # `npm start` / `npm run <script>` / `yarn dev` / `pnpm run dev`
        if head in ("npm", "yarn", "pnpm"):
            return (self._resolve_via_package_json(project_root, tokens), True)
        return (None, False)

    def _resolve_via_package_json(self, project_root, tokens):
        pkg_path = os.path.join(project_root, "package.json")
        if not os.path.isfile(pkg_path):
            return None
        try:
            with open(pkg_path, "r", encoding="utf-8") as f:
                pkg = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        # Try scripts.<script_name> first when this is `npm run <name>` or
        # `npm start` (which is sugar for `npm run start`).
        scripts = pkg.get("scripts") or {}
        script_name = self._derive_script_name(tokens)
        script_cmd = scripts.get(script_name) if script_name else None
        if script_cmd:
            inner = self._entry_from_inner_command(script_cmd)
            if inner is not None:
                return inner
        # Fallback: package.json main / module / exports["."]["import"].
        main = pkg.get("main")
        if isinstance(main, str) and main:
            return main
        module = pkg.get("module")
        if isinstance(module, str) and module:
            return module
        exports = pkg.get("exports")
        if isinstance(exports, dict):
            dot = exports.get(".")
            if isinstance(dot, dict):
                for key in ("import", "default", "require"):
                    val = dot.get(key)
                    if isinstance(val, str) and val:
                        return val
            elif isinstance(dot, str):
                return dot
        return None

    def _derive_script_name(self, tokens):
        """`npm start` → "start"; `npm run dev` → "dev"; otherwise None."""
        if len(tokens) >= 2 and tokens[0] in ("npm", "yarn", "pnpm"):
            if tokens[1] == "run" and len(tokens) >= 3:
                return tokens[2]
            if tokens[1] == "start":
                return "start"
            if tokens[1] in ("dev", "build", "test"):
                return tokens[1]
        return None

    def _entry_from_inner_command(self, script_cmd):
        """Best-effort: if the script is `node foo.js` or `tsx foo.ts`, pull
        out the entry file; otherwise punt to the package.json fallback."""
        try:
            inner_tokens = shlex.split(script_cmd)
        except ValueError:
            inner_tokens = script_cmd.split()
        if not inner_tokens:
            return None
        head = inner_tokens[0]
        if head in ("node", "tsx", "ts-node"):
            for tok in inner_tokens[1:]:
                if tok.startswith("-"):
                    continue
                return tok
        return None

    # ---- specifier resolution --------------------------------------------

    def _resolve_specifier(self, spec, from_file_rel, project_root, limitations):
        """Workspace-relative path or None. Bare modules / absolute paths skip
        silently; path aliases emit one limitation each."""
        if spec.startswith("@/") or spec.startswith("~/"):
            limitations.append(
                f"path alias {spec!r} not resolved (no tsconfig.json parsing in Stage F)"
            )
            return None
        if spec.startswith("/"):
            return None
        if not (spec.startswith("./") or spec.startswith("../") or spec == "." or spec == ".."):
            # Bare specifier — node_modules dependency, not workspace.
            return None
        from_dir = os.path.dirname(from_file_rel)
        joined_rel = os.path.normpath(os.path.join(from_dir, spec))
        joined_abs = os.path.join(project_root, joined_rel)

        # Direct file match (with or without extension).
        if os.path.isfile(joined_abs):
            return joined_rel
        for ext in _EXTENSIONS:
            candidate_abs = joined_abs + ext
            if os.path.isfile(candidate_abs):
                return joined_rel + ext
        # Directory + index.* match.
        if os.path.isdir(joined_abs):
            for index_name in _INDEX_FILES:
                candidate_abs = os.path.join(joined_abs, index_name)
                if os.path.isfile(candidate_abs):
                    return os.path.join(joined_rel, index_name)
        return None
