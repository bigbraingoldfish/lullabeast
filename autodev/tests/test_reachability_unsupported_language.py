"""P1 Stage F — registry behaviour for unsupported / test-runner / npx commands.

Direction reminder 2 (language-specific tooling degrades gracefully) is the
load-bearing rule here: the pipeline must accept any project_type and any
entry command. When the resolver registry has no language match, it returns
None — the gate helper then emits a single ``no_resolver`` advisory, and the
phase still passes.

Test-runner entries (pytest, jest, vitest, ...) take a separate path: the
classifier returns ``"test_runner"`` and the gate helper emits a
``reachability_not_applicable`` signal instead of a warning — distinct from
"we couldn't analyse" and from "we don't support this language."

The ``npx`` wrapper is stripped before classification: ``npx vite`` is a
js_ts command (vite); only bare ``npx`` falls through to ``"unsupported"``.
"""

import pytest

from reachability import classify_command, get_resolver  # noqa: E402


# ---------------------------------------------------------------------------
# Unsupported languages — direction reminder 2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "cargo run",
        "go run main.go",
        "java -jar app.jar",
        "ruby script.rb",
        "make build",
    ],
)
def test_unsupported_command_returns_no_resolver(command):
    """Every non-Python / non-JS-TS / non-test-runner command must classify
    as ``unsupported`` and return None from get_resolver. The gate-helper
    (separately tested) emits a ``no_resolver`` advisory — never an error."""
    assert classify_command(command) == "unsupported"
    assert get_resolver(command, "/tmp/anywhere") is None


def test_rust_entry_point_classifies_unsupported():
    """The verbatim test name from the roadmap's Stage F TDD plan."""
    assert classify_command("cargo run") == "unsupported"
    assert get_resolver("cargo run", "/tmp/anywhere") is None


# ---------------------------------------------------------------------------
# Test-runner family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "pytest tests/",
        "pytest -k smoke",
        "unittest discover",
        "jest --watch",
        "vitest run",
        "mocha test/**/*.spec.js",
        "playwright test",
        "cypress run",
    ],
)
def test_test_runner_classification(command):
    """Test runners take a separate path so the operator sees the
    'consciously skipped' signal (reachability_not_applicable), distinct
    from 'no resolver' and from 'resolver error.'"""
    assert classify_command(command) == "test_runner"
    assert get_resolver(command, "/tmp/anywhere") is None


# ---------------------------------------------------------------------------
# npx wrapper — strip and re-classify
# ---------------------------------------------------------------------------


def test_npx_vite_strips_to_js_ts():
    """`npx vite` is just vite. Without the strip, the registry would emit
    a false ``no_resolver`` warning on a perfectly analysable JS entry."""
    assert classify_command("npx vite") == "js_ts"
    assert get_resolver("npx vite", "/tmp/anywhere") is not None


def test_npx_tsx_strips_to_js_ts():
    assert classify_command("npx tsx app.ts") == "js_ts"


def test_npx_ts_node_strips_to_js_ts():
    assert classify_command("npx ts-node script.ts") == "js_ts"


def test_npx_pytest_strips_to_test_runner():
    """The strip is consistent — npx + test runner still classifies as
    test_runner."""
    assert classify_command("npx pytest") == "test_runner"


def test_bare_npx_falls_through_to_unsupported():
    """`npx` with no wrapped tool can't be analysed — fall through to
    unsupported so the operator sees a no_resolver advisory."""
    assert classify_command("npx") == "unsupported"


# ---------------------------------------------------------------------------
# Empty / missing
# ---------------------------------------------------------------------------


def test_empty_command_classification():
    assert classify_command("") == "empty"
    assert classify_command("   ") == "empty"
    assert get_resolver("", "/tmp/anywhere") is None


# ---------------------------------------------------------------------------
# Positive controls — confirm classify_command is not over-eager
# ---------------------------------------------------------------------------


def test_python_command_classifies_python():
    assert classify_command("python app.py") == "python"
    assert classify_command("python3 main.py") == "python"
    assert classify_command("python -m pkg.mod") == "python"
    assert classify_command("uvicorn server:app") == "python"
    assert classify_command("flask run") == "python"
    assert classify_command("gunicorn app:app") == "python"


def test_js_ts_command_classifies_js_ts():
    assert classify_command("node app.js") == "js_ts"
    assert classify_command("npm start") == "js_ts"
    assert classify_command("vite") == "js_ts"
    assert classify_command("yarn dev") == "js_ts"
    assert classify_command("pnpm run dev") == "js_ts"
