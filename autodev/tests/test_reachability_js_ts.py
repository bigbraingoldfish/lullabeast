"""P1 Stage F — JS/TS reachability resolver.

The JS/TS resolver performs a regex-based static walk (no AST parser dep) over
``import`` / ``require`` / ``export ... from`` statements, reads
``package.json``'s ``main`` / ``module`` / ``exports`` to derive the entry,
and resolves relative specifiers across .ts/.tsx/.js/.jsx/.mjs/.cjs +
/index.* with TS-first ordering.

Documented limitations the tests pin:
* Dynamic ``import(varname)`` (non-literal) emits one limitation per file.
* Path aliases (``@/foo``, ``~/foo``) emit one limitation per occurrence.
* Bare module specifiers (``react``, ``@scope/pkg``) are skipped silently.
"""

import json
import os

from reachability import JsTsResolver  # noqa: E402


def _write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)


def _norm(rel_set):
    return {os.path.normpath(p) for p in rel_set}


def test_package_main_entry(tmp_path):
    """`npm start` falls through to package.json["main"] when there's no
    explicit scripts.start entry."""
    root = str(tmp_path)
    _write(
        os.path.join(root, "package.json"),
        json.dumps({"name": "test", "main": "src/index.js"}),
    )
    _write(
        os.path.join(root, "src", "index.js"),
        "const a = require('./a');\n",
    )
    _write(os.path.join(root, "src", "a.js"), "")
    result = JsTsResolver().resolve(root, "npm start")
    reachable = _norm(result.reachable)
    assert "package.json" in reachable, (
        "the resolver derived the entry from package.json, so package.json "
        "is part of the reachable surface"
    )
    assert os.path.normpath("src/index.js") in reachable
    assert os.path.normpath("src/a.js") in reachable


def test_es_module_import_chain(tmp_path):
    """ES-module `import { foo } from './a'` resolves the relative specifier
    and follows the chain."""
    root = str(tmp_path)
    _write(
        os.path.join(root, "index.ts"),
        "import { foo } from './a';\nfoo();\n",
    )
    _write(os.path.join(root, "a.ts"), "export const foo = () => 1;\n")
    result = JsTsResolver().resolve(root, "node index.ts")
    reachable = _norm(result.reachable)
    assert "index.ts" in reachable
    assert "a.ts" in reachable


def test_es_module_export_chain(tmp_path):
    """`export { foo } from './a'` (re-export) follows like a regular import."""
    root = str(tmp_path)
    _write(os.path.join(root, "b.ts"), "export { foo } from './a';\n")
    _write(os.path.join(root, "a.ts"), "export const foo = 1;\n")
    result = JsTsResolver().resolve(root, "node b.ts")
    reachable = _norm(result.reachable)
    assert "b.ts" in reachable
    assert "a.ts" in reachable


def test_bare_modules_skipped_silently(tmp_path):
    """`import React from 'react'` is a node_modules dependency, not a
    workspace file. The resolver must NOT emit a limitation for every bare
    specifier — that would swamp output on any real project."""
    root = str(tmp_path)
    _write(
        os.path.join(root, "index.js"),
        "import React from 'react';\nimport { x } from '@scope/pkg';\n",
    )
    result = JsTsResolver().resolve(root, "node index.js")
    assert result.limitations == [], (
        f"bare specifiers must skip silently; got: {result.limitations!r}"
    )


def test_path_alias_emits_limitation(tmp_path):
    """`@/foo` requires a tsconfig.json paths section to resolve. Stage F
    has no tsconfig parsing — emit a limitation, skip the specifier."""
    root = str(tmp_path)
    _write(
        os.path.join(root, "index.ts"),
        "import { foo } from '@/utils';\n",
    )
    result = JsTsResolver().resolve(root, "node index.ts")
    assert any("@/" in lim or "alias" in lim.lower() for lim in result.limitations)


def test_require_cjs_chain(tmp_path):
    """`require('./a')` (CommonJS) is followed identically to ES import."""
    root = str(tmp_path)
    _write(
        os.path.join(root, "index.js"),
        "const a = require('./a');\n",
    )
    _write(os.path.join(root, "a.js"), "module.exports = 1;\n")
    result = JsTsResolver().resolve(root, "node index.js")
    reachable = _norm(result.reachable)
    assert "index.js" in reachable and "a.js" in reachable


def test_index_resolution(tmp_path):
    """`import { x } from './foo'` where `./foo/index.ts` exists."""
    root = str(tmp_path)
    _write(
        os.path.join(root, "index.ts"),
        "import { x } from './foo';\n",
    )
    _write(os.path.join(root, "foo", "index.ts"), "export const x = 1;\n")
    result = JsTsResolver().resolve(root, "node index.ts")
    reachable = _norm(result.reachable)
    assert "index.ts" in reachable
    assert os.path.normpath("foo/index.ts") in reachable


def test_dynamic_import_with_variable_emits_limitation(tmp_path):
    """`import(someVar)` cannot be statically resolved — emit one limitation
    per file rather than blocking."""
    root = str(tmp_path)
    _write(
        os.path.join(root, "index.js"),
        "const name = 'foo';\nconst mod = import(name);\n",
    )
    result = JsTsResolver().resolve(root, "node index.js")
    assert any(
        "dynamic" in lim.lower() or "import" in lim.lower()
        for lim in result.limitations
    )
