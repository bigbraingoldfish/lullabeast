"""PREREQ-5 — round-trip guard between the converter's output format and the
PREREQ-1 parser.

A ``## Prerequisites`` block authored in the grammar the roadmap-generation skill
documents (em-dash separators, ``(config|secret)`` kind, ``needed by`` / ``used
by`` scope, names-only) must parse cleanly via ``prereq_spec.parse_prerequisites``.

This pins the contract seam between PREREQ-5 (the converter authors the block)
and PREREQ-1 (the parser reads it): if either side's format drifts, this turns
red. It cannot exercise the live LLM — it guards the *documented format* both
sides agree on, which is the honest, useful boundary without a model in the loop.
"""
import json

# Path wiring (autodev/pipeline on sys.path) handled by conftest.py
import prereq_spec


# A verification.md whose ## Prerequisites block conforms to the grammar the
# roadmap-generation SKILL.md documents — names/types/purposes only, em-dash sep.
CONVERTER_OUTPUT = (
    "# Verification\n\n"
    "## Project type\n"
    "http-api\n\n"
    "## Entry point\n"
    "- Command: `uvicorn app:app`\n"
    "- Ready signal: HTTP 200 from http://localhost:8000/health\n\n"
    "## Prerequisites\n\n"
    "### Tools\n"
    "- python — Python 3.11+ runtime — needed by all\n"
    "- docker — container runtime for the test DB — needed by DATA-E1\n\n"
    "### Environment\n"
    "- API_BASE_URL (config) — base URL the app calls — used by all\n"
    "- STRIPE_API_KEY (secret) — provider key for payments — used by API-E2\n\n"
    "## Public surface\n- create a charge\n"
)


def test_converter_block_round_trips_clean():
    """The documented converter output parses to the exact PREREQ-1 shape with
    no warnings — block_present True, every field mapped, kinds preserved."""
    spec = prereq_spec.parse_prerequisites(CONVERTER_OUTPUT)
    assert spec["block_present"] is True
    assert spec["warnings"] == []
    assert spec["tools"] == [
        {"name": "python", "description": "Python 3.11+ runtime", "needed_by": "all"},
        {
            "name": "docker",
            "description": "container runtime for the test DB",
            "needed_by": "DATA-E1",
        },
    ]
    assert spec["env"] == [
        {
            "name": "API_BASE_URL",
            "kind": "config",
            "purpose": "base URL the app calls",
            "used_by": "all",
        },
        {
            "name": "STRIPE_API_KEY",
            "kind": "secret",
            "purpose": "provider key for payments",
            "used_by": "API-E2",
        },
    ]


def test_leaked_value_never_survives_round_trip():
    """Safety spine (DEC-2): even if a value leaked into the converter output, the
    parser strips it — the value never appears in the parsed spec, the name still
    parses, and a warning is recorded."""
    leaky = CONVERTER_OUTPUT.replace(
        "- STRIPE_API_KEY (secret) — provider key for payments — used by API-E2",
        "- STRIPE_API_KEY (secret) = sk_live_abc123 — provider key for payments — used by API-E2",
    )
    spec = prereq_spec.parse_prerequisites(leaky)
    blob = json.dumps(spec)
    assert "sk_live_abc123" not in blob, "a value must never survive parsing"
    assert "STRIPE_API_KEY" in [e["name"] for e in spec["env"]], (
        "the name must still parse after the value is stripped"
    )
    assert any(
        "value-shaped" in w.lower() or "stripped" in w.lower() for w in spec["warnings"]
    ), "stripping a value must record a warning"
