"""PREREQ-1 — prereq_spec.parse_prerequisites.

Value-safe parser for the ``## Prerequisites`` block of verification.md.
Clones the section-extraction approach of ``phase_resolver._extract_entry_point``
(deterministic, never raises) but takes TEXT, not a path: a function that never
opens a file cannot leak one, which is the spine of the never-ingest-a-value
invariant (roadmap DEC-2).

The canonical block (roadmap reference, lines 411-428) is::

    ## Prerequisites

    ### Tools
    - node — Node.js 20+ runtime — needed by all
    - unity6 — Unity 6 LTS + Android Build Support — needed by INFRA-1

    ### Environment
    - API_BASE_URL (config) — base URL the app calls — used by all
    - DATABASE_URL (config) — Postgres connection string — used by DATA-2
    - OPENAI_API_KEY (secret) — provider key for the app's LLM calls — used by CORE-3

Separator is the em-dash (U+2014), matching the authored format — a hyphen line
is deliberately treated as malformed (D3).
"""

import json

import prereq_spec


# An absent/omitted block — `block_present` False. Under the conditionally-
# required authoring rule this is a contract violation (the converter must
# always emit the section), surfaced for PREREQ-3 to warn on; it is NOT the same
# as a deliberate `none`.
ABSENT = {"tools": [], "env": [], "warnings": [], "block_present": False}
# A present block that deliberately declares nothing (`none`) — `block_present`
# True. The section WAS emitted; it just declares nothing.
NONE_DECLARED = {"tools": [], "env": [], "warnings": [], "block_present": True}

# The canonical reference block, verbatim from the roadmap (names/types/purposes only).
CANONICAL_BLOCK = (
    "# Verification\n\n"
    "## Entry point\n"
    "- Command: `npm start`\n\n"
    "## Prerequisites\n\n"
    "### Tools\n"
    "- node — Node.js 20+ runtime — needed by all\n"
    "- unity6 — Unity 6 LTS + Android Build Support — needed by INFRA-1\n\n"
    "### Environment\n"
    "- API_BASE_URL (config) — base URL the app calls — used by all\n"
    "- DATABASE_URL (config) — Postgres connection string — used by DATA-2\n"
    "- OPENAI_API_KEY (secret) — provider key for the app's LLM calls — used by CORE-3\n\n"
    "## Public surface\n- x\n"
)


# ---------------------------------------------------------------------------
# T1 — full block → exact shape
# ---------------------------------------------------------------------------


def test_full_block_exact_shape():
    """The canonical block parses to the exact documented shape: every field
    mapped to the right segment, kinds preserved, no spurious warnings. This is
    the contract every downstream phase (PREREQ-3/5/6) reads — a field-mapping
    regression here corrupts the whole feature."""
    spec = prereq_spec.parse_prerequisites(CANONICAL_BLOCK)
    assert spec["tools"] == [
        {"name": "node", "description": "Node.js 20+ runtime", "needed_by": "all"},
        {
            "name": "unity6",
            "description": "Unity 6 LTS + Android Build Support",
            "needed_by": "INFRA-1",
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
            "name": "DATABASE_URL",
            "kind": "config",
            "purpose": "Postgres connection string",
            "used_by": "DATA-2",
        },
        {
            "name": "OPENAI_API_KEY",
            "kind": "secret",
            "purpose": "provider key for the app's LLM calls",
            "used_by": "CORE-3",
        },
    ]
    assert spec["warnings"] == []
    assert spec["block_present"] is True  # a real block was emitted


# ---------------------------------------------------------------------------
# T2 — absent block → empty spec, no raise (DEC-3 non-breaking invariant)
# ---------------------------------------------------------------------------


def test_absent_block_returns_empty_spec():
    """No ## Prerequisites section → empty tools/env, never raises (DEC-3
    non-breaking) AND block_present False. Under the conditionally-required
    rule, an absent block is a contract violation (the converter must always
    emit it), so it must be distinguishable from a deliberate `none` — the flag
    lets PREREQ-3 surface a 'missing required section' warning."""
    body = (
        "# Verification\n\n"
        "## Entry point\n- Command: `python app.py`\n\n"
        "## Public surface\n- thing\n"
    )
    assert prereq_spec.parse_prerequisites(body) == ABSENT


def test_absent_block_is_distinct_from_none_declaration():
    """The core distinction the conditionally-required rule creates: an absent
    block (violation) must NOT read identically to a deliberate `none` (valid
    empty). Both yield empty tools/env; only `block_present` separates them. If
    this regresses, PREREQ-3 cannot tell 'converter omitted the required block'
    from 'project genuinely needs nothing', and the whole point of requiring the
    section is lost."""
    absent = prereq_spec.parse_prerequisites(
        "# Verification\n## Entry point\n- Command: `x`\n"
    )
    declared_none = prereq_spec.parse_prerequisites(
        "## Prerequisites\n\n### Tools\n- none\n"
    )
    assert absent["block_present"] is False
    assert declared_none["block_present"] is True
    assert absent != declared_none
    # The distinction is purely block_present — both carry no tools/env.
    assert absent["tools"] == absent["env"] == []
    assert declared_none["tools"] == declared_none["env"] == []


# ---------------------------------------------------------------------------
# T3 / T4 — partial specs (subsection isolation)
# ---------------------------------------------------------------------------


def test_tools_only_partial():
    """A block with only ### Tools yields tools, empty env. Guards against an
    env parser running on absent input and inventing rows."""
    body = (
        "## Prerequisites\n\n"
        "### Tools\n"
        "- git — version control — needed by all\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    assert spec["tools"] == [
        {"name": "git", "description": "version control", "needed_by": "all"}
    ]
    assert spec["env"] == []
    assert spec["warnings"] == []


def test_env_only_partial():
    """Symmetric to tools-only — only ### Environment present."""
    body = (
        "## Prerequisites\n\n"
        "### Environment\n"
        "- API_BASE_URL (config) — base URL — used by all\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    assert spec["env"] == [
        {
            "name": "API_BASE_URL",
            "kind": "config",
            "purpose": "base URL",
            "used_by": "all",
        }
    ]
    assert spec["tools"] == []
    assert spec["warnings"] == []


# ---------------------------------------------------------------------------
# T5 — malformed line skipped + warned (no silent drop, no garbage row)
# ---------------------------------------------------------------------------


def test_malformed_line_skipped_and_warned():
    """A line missing the required segments is skipped (not stored as a junk
    row) AND recorded in warnings — a silent drop would hide an authoring
    mistake from the operator."""
    body = (
        "## Prerequisites\n\n"
        "### Environment\n"
        "- JUST_A_NAME\n"
        "- API_BASE_URL (config) — base URL — used by all\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    # The good line still parses.
    assert spec["env"] == [
        {
            "name": "API_BASE_URL",
            "kind": "config",
            "purpose": "base URL",
            "used_by": "all",
        }
    ]
    # The bad line is gone but left a warning that references it.
    assert any("JUST_A_NAME" in w for w in spec["warnings"])


# ---------------------------------------------------------------------------
# T6 — value injection stripped (SAFETY-CRITICAL: the never-ingest invariant)
# ---------------------------------------------------------------------------


def test_value_injection_stripped():
    """If a value-shaped ``= sk-...`` payload is injected into a declaration
    line, the value must NOT survive anywhere in the parsed result, and a
    warning must flag it. This is the entire reason the value guard exists; if
    this regresses, a secret could be captured into verification.md."""
    body = (
        "## Prerequisites\n\n"
        "### Environment\n"
        "- API_KEY (secret) = sk-abc123 — provider key — used by all\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    # The value must be absent from the ENTIRE serialized structure.
    assert "sk-abc123" not in json.dumps(spec)
    # The name/kind/scope still parse — we keep the contract, drop the value.
    assert spec["env"] == [
        {
            "name": "API_KEY",
            "kind": "secret",
            "purpose": "provider key",
            "used_by": "all",
        }
    ]
    # A warning names the offending key.
    assert any("API_KEY" in w for w in spec["warnings"])


# ---------------------------------------------------------------------------
# T7 / T8 — kind defaulting and passthrough
# ---------------------------------------------------------------------------


def test_kind_defaults_to_config():
    """An env line with no ``(kind)`` defaults to config — never None, never a
    crash (PREREQ-6 renders a kind badge off this)."""
    body = (
        "## Prerequisites\n\n"
        "### Environment\n"
        "- SOME_VAR — a purpose — used by all\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    assert spec["env"] == [
        {"name": "SOME_VAR", "kind": "config", "purpose": "a purpose", "used_by": "all"}
    ]
    assert spec["warnings"] == []


def test_kind_secret_preserved():
    """An explicit ``(secret)`` is preserved verbatim."""
    body = (
        "## Prerequisites\n\n"
        "### Environment\n"
        "- TOKEN (secret) — auth token — used by CORE-1\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    assert spec["env"][0]["kind"] == "secret"


def test_unknown_kind_defaults_config_with_warning():
    """An out-of-enum kind degrades to config and warns — never reaches the UI
    badge as an unknown value."""
    body = (
        "## Prerequisites\n\n"
        "### Environment\n"
        "- WEIRD (banana) — purpose — used by all\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    assert spec["env"][0]["kind"] == "config"
    assert any("WEIRD" in w for w in spec["warnings"])


# ---------------------------------------------------------------------------
# T9 — section termination (don't slurp a later section's bullets)
# ---------------------------------------------------------------------------


def test_terminates_at_next_h2_section():
    """A bullet under a LATER ## section must not be parsed into the spec —
    the same boundary guard _extract_entry_point relies on."""
    body = (
        "## Prerequisites\n\n"
        "### Tools\n"
        "- node — Node runtime — needed by all\n\n"
        "## Public surface\n"
        "- evil_tool — should not be parsed — needed by all\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    assert spec["tools"] == [
        {"name": "node", "description": "Node runtime", "needed_by": "all"}
    ]


def test_bullets_before_any_subsection_are_ignored():
    """A bullet directly under ## Prerequisites but before any ### subsection is
    ignored (the ``none``-as-bare-line and stray-prose cases). No row, no crash."""
    body = (
        "## Prerequisites\n\n"
        "- stray bullet with no subsection\n\n"
        "### Tools\n"
        "- node — Node runtime — needed by all\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    assert spec["tools"] == [
        {"name": "node", "description": "Node runtime", "needed_by": "all"}
    ]
    assert spec["env"] == []


# ---------------------------------------------------------------------------
# T10 — `none` sentinel → empty, no warning (PREREQ-5 round-trip)
# ---------------------------------------------------------------------------


def test_none_sentinel_is_empty_no_warning():
    """PREREQ-5 always emits the (now required) block, using ``none`` when the
    project needs nothing. ``- none`` parses to empty lists with NO warning AND
    block_present True — it is a valid, intentional declaration that the section
    was emitted and declares nothing, NOT a malformed line and NOT an absent
    block (see test_absent_block_is_distinct_from_none_declaration)."""
    body = (
        "## Prerequisites\n\n"
        "### Tools\n"
        "- none\n\n"
        "### Environment\n"
        "- none\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    assert spec == NONE_DECLARED


# ---------------------------------------------------------------------------
# T11 — defensive input (no raise on None/empty/non-str)
# ---------------------------------------------------------------------------


def test_non_string_and_empty_input():
    """PREREQ-3 may hand us the result of a read that returned None. Defensive
    inputs (None / empty / non-str) mean there is no document and therefore no
    block: empty spec, block_present False, never raises."""
    assert prereq_spec.parse_prerequisites(None) == ABSENT
    assert prereq_spec.parse_prerequisites("") == ABSENT
    assert prereq_spec.parse_prerequisites(123) == ABSENT


# ---------------------------------------------------------------------------
# T12 — em-dash inside a description is preserved (name=first, scope=last)
# ---------------------------------------------------------------------------


def test_em_dash_inside_description_preserved():
    """Splitting on the em-dash must not truncate a description that itself
    contains an em-dash: name is the first segment, scope the last, and the
    middle is rejoined verbatim."""
    body = (
        "## Prerequisites\n\n"
        "### Tools\n"
        "- ripgrep — fast — recursive search — needed by all\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    assert spec["tools"] == [
        {
            "name": "ripgrep",
            "description": "fast — recursive search",
            "needed_by": "all",
        }
    ]


# ---------------------------------------------------------------------------
# T13 — hyphen-separated line is malformed (pins the D3 separator contract)
# ---------------------------------------------------------------------------


def test_hyphen_separated_line_is_malformed():
    """The separator is the em-dash (U+2014), not a hyphen. A hyphen line is
    treated as malformed: skipped + warned. This test pins D3 so any future
    'be lenient about separators' change is a conscious test edit, not an
    accidental silent mis-parse."""
    body = (
        "## Prerequisites\n\n"
        "### Tools\n"
        "- node - Node runtime - needed by all\n"
    )
    spec = prereq_spec.parse_prerequisites(body)
    assert spec["tools"] == []
    assert spec["warnings"] != []
