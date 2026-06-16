"""Drift / completeness guards for the centralized error-code taxonomy (LAUNCH-7).

``autodev/pipeline/error_codes.py`` is the single source of truth for ``ERR_*``
codes. These tests pin the invariants that keep it that way:

1. every constant's value equals its name (no fat-fingered literal in the module);
2. ``ALL_ERROR_CODES`` is exactly the set of defined constants;
3. no inline quoted ``"ERR_..."`` literal survives in the production pipeline
   source — proves the LAUNCH-7 replacement is complete, and fails CI the moment
   a future inline literal is reintroduced (forcing it to become a constant);
4. every ``ERR_*`` token referenced by the dashboard (``ui/index.html``) is a
   defined code — catches backend/UI taxonomy drift.
"""
import glob
import os
import re
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIPELINE = os.path.join(_REPO, "autodev", "pipeline")
if _PIPELINE not in sys.path:
    sys.path.insert(0, _PIPELINE)

import error_codes  # noqa: E402

_QUOTED_ERR_RE = re.compile(r"""['"]ERR_[A-Z_]+['"]""")
_ERR_TOKEN_RE = re.compile(r"ERR_[A-Z_]+")

# Production source that must contain NO inline quoted ERR_ literal post-LAUNCH-7
# (error_codes.py itself — the one legitimate home of the literals — is excluded).
_PRODUCTION_SOURCES = [os.path.join(_PIPELINE, "orchestrator.py")] + sorted(
    glob.glob(os.path.join(_PIPELINE, "gate_scripts", "*.py"))
)


def _err_constants():
    return {
        name: getattr(error_codes, name)
        for name in dir(error_codes)
        if name.startswith("ERR_")
    }


def test_every_constant_value_equals_its_name():
    consts = _err_constants()
    assert consts, "error_codes defines no ERR_* constants"
    for name, value in consts.items():
        assert value == name, f"{name} has value {value!r}; constants must be self-named"


def test_all_error_codes_matches_the_defined_constants():
    assert error_codes.ALL_ERROR_CODES == frozenset(_err_constants().values())


def test_no_inline_quoted_err_literal_in_production_source():
    offenders = []
    for path in _PRODUCTION_SOURCES:
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if _QUOTED_ERR_RE.search(line):
                    offenders.append(f"  {os.path.relpath(path, _REPO)}:{lineno}  {line.strip()}")
    assert not offenders, (
        "inline quoted ERR_* literal(s) found — import the constant from error_codes "
        "instead:\n" + "\n".join(offenders)
    )


def test_ui_error_code_tokens_are_all_defined():
    with open(os.path.join(_REPO, "ui", "index.html"), encoding="utf-8") as f:
        tokens = set(_ERR_TOKEN_RE.findall(f.read()))
    unknown = tokens - error_codes.ALL_ERROR_CODES
    assert not unknown, (
        "ui/index.html references ERR_* code(s) absent from the taxonomy "
        f"(backend/UI drift): {sorted(unknown)}"
    )
