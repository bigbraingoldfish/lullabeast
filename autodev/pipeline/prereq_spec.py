"""Value-safe parser for the ``verification.md`` ``## Prerequisites`` block.

This clones the section-extraction *approach* of
``gate_scripts/phase_resolver.py``'s ``_extract_entry_point`` — module-level
regexes, scan to the next section header, never raise — but takes the document
**text**, not a path. The function performs no file I/O on purpose: a parser
that cannot open a file cannot leak one, which is the spine of the
never-ingest-a-value invariant (roadmap PREREQ ``DEC-2``). Consumers
(the UI server and the orchestrator, PREREQ-3) read ``verification.md``
themselves and pass the text in.

It is deterministic — no network, no LLM, no clock — so the same input always
yields the same spec.

Declaration format (names / types / purposes only — **never values**)::

    ## Prerequisites

    ### Tools
    - <name> — <description> — needed by <PHASE-ID|all>

    ### Environment
    - <NAME> (config|secret) — <purpose> — used by <PHASE-ID|all>

The separator is the em-dash ``—`` (U+2014), matching the authored template; a
hyphen-separated line is treated as malformed (it is skipped and warned), so
parsing stays unambiguous when a description legitimately contains hyphens.

Returned shape (``PrereqSpec``, a plain JSON-serializable dict)::

    {
      "tools":         [{"name", "description", "needed_by"}],
      "env":           [{"name", "kind", "purpose", "used_by"}],  # kind ∈ {config, secret}
      "warnings":      ["..."],
      "block_present": bool,  # True iff a `## Prerequisites` heading was found —
                              # a deliberate `none` is (True, empty); an absent
                              # block is (False, empty), a contract violation under
                              # the conditionally-required authoring rule.
    }
"""

import re
from typing import List, Optional, Tuple, TypedDict


class ToolReq(TypedDict):
    name: str
    description: str
    needed_by: str


class EnvReq(TypedDict):
    name: str
    kind: str  # "config" | "secret"
    purpose: str
    used_by: str


class PrereqSpec(TypedDict):
    tools: List[ToolReq]
    env: List[EnvReq]
    warnings: List[str]
    # True iff a ``## Prerequisites`` heading was found. Distinguishes a
    # deliberate ``none`` declaration (present, empty) from an absent/omitted
    # block — a contract violation under the conditionally-required authoring
    # rule, which PREREQ-3 surfaces as a non-blocking warning.
    block_present: bool


# The canonical declaration-line separator (D3). Defined once so the regexes
# that exclude it stay in sync with the joiner that rebuilds multi-segment text.
_EM_DASH = "—"  # —

# Section / subsection headers. Case-insensitive for authoring robustness; the
# literal section word still has to match, so ``## Public surface`` cannot be
# mistaken for ``## Prerequisites``.
_PREREQ_H2_RE = re.compile(r"^\s*##\s+Prerequisites\s*$", re.IGNORECASE)
_TOOLS_H3_RE = re.compile(r"^\s*###\s+Tools\s*$", re.IGNORECASE)
_ENV_H3_RE = re.compile(r"^\s*###\s+Environment\s*$", re.IGNORECASE)
# ``##`` + a space matches only an H2 (``### Tools`` has ``#`` after ``##``, no
# space, so it is *not* caught here — H3 detection stays separate).
_NEXT_H2_RE = re.compile(r"^\s*##\s+")
_NEXT_H3_RE = re.compile(r"^\s*###\s+")
_BULLET_RE = re.compile(r"^\s*-\s+(.*\S)\s*$")  # a list bullet with non-empty body

# ``NAME`` or ``NAME (kind)`` for the first segment of an env line.
_NAME_KIND_RE = re.compile(r"^([^()\s]+)\s*(?:\(([^)]+)\))?\s*$")
# A value-shaped ``= payload`` assignment, up to (not including) the next
# em-dash or end of line. Used to excise an injected value (belt-and-suspenders).
_VALUE_ASSIGN_RE = re.compile(r"=[^" + _EM_DASH + r"]*")
# The leading bare token of a line — used only to name the offending key in a
# value-stripped warning.
_LEAD_TOKEN_RE = re.compile(r"\s*([^\s(=" + _EM_DASH + r"]+)")
# ``none`` / ``n/a`` / a lone dash — an intentional "nothing here" declaration.
_NONE_RE = re.compile(r"^(none|n/?a|" + _EM_DASH + r"|-)$", re.IGNORECASE)

_VALID_KINDS = ("config", "secret")
_DEFAULT_KIND = "config"


def _name_guess(body: str) -> str:
    """Return the leading bare token of a line, for naming it in a warning."""
    m = _LEAD_TOKEN_RE.match(body)
    return m.group(1) if m else "?"


def _strip_value_payload(body: str) -> Tuple[str, Optional[str]]:
    """Belt-and-suspenders value guard. If a line carries an ``=``-assignment
    (a value-shaped payload), strip from the first ``=`` up to the next em-dash
    and return a warning naming the key. We never want a value to survive, so
    the error direction is deliberately toward over-stripping. Lines with no
    ``=`` are returned unchanged with no warning (the common path)."""
    if "=" not in body:
        return body, None
    name = _name_guess(body)
    cleaned = _VALUE_ASSIGN_RE.sub("", body, count=1)
    return cleaned, "value-shaped content stripped from {}".format(name)


def _segments(body: str) -> List[str]:
    """Split a declaration body on the em-dash and trim each segment."""
    return [p.strip() for p in body.split(_EM_DASH)]


def _join_middle(segments: List[str]) -> str:
    """Rebuild the description/purpose from the segments between name and scope,
    re-inserting the em-dash so a value that legitimately contained one is not
    lost (name = first segment, scope = last segment)."""
    return (" " + _EM_DASH + " ").join(segments[1:-1]).strip()


def _strip_scope_prefix(segment: str, prefix: str) -> str:
    """Strip a leading ``needed by`` / ``used by`` (case-insensitive) from the
    scope segment, leaving the bare phase id (or ``all``)."""
    s = segment.strip()
    if s.lower().startswith(prefix):
        return s[len(prefix):].strip()
    return s


def _parse_tool_line(body: str) -> Tuple[Optional[ToolReq], Optional[str]]:
    """Parse one ``### Tools`` bullet into a ToolReq, or ``(None, warning)`` for
    a ``none`` sentinel (no warning) or a malformed line (warned)."""
    cleaned, warning = _strip_value_payload(body)
    if _NONE_RE.match(cleaned.strip()):
        return None, None
    segs = _segments(cleaned)
    if len(segs) < 3 or not segs[0]:
        return None, warning or "malformed tool prerequisite skipped: {}".format(body)
    entry: ToolReq = {
        "name": segs[0],
        "description": _join_middle(segs),
        "needed_by": _strip_scope_prefix(segs[-1], "needed by"),
    }
    return entry, warning


def _parse_env_line(body: str) -> Tuple[Optional[EnvReq], Optional[str]]:
    """Parse one ``### Environment`` bullet into an EnvReq, or ``(None, warning)``
    for a ``none`` sentinel (no warning) or a malformed line (warned). ``kind``
    defaults to ``config`` when absent and degrades to ``config`` (with a
    warning) when it is not one of ``{config, secret}``."""
    cleaned, warning = _strip_value_payload(body)
    if _NONE_RE.match(cleaned.strip()):
        return None, None
    segs = _segments(cleaned)
    if len(segs) < 3 or not segs[0]:
        return None, warning or "malformed env prerequisite skipped: {}".format(body)
    m = _NAME_KIND_RE.match(segs[0])
    if not m:
        return None, warning or "malformed env prerequisite skipped: {}".format(body)
    name = m.group(1)
    kind = (m.group(2) or _DEFAULT_KIND).strip().lower()
    if kind not in _VALID_KINDS:
        warning = warning or "unknown kind '{}' for {} — defaulted to config".format(
            kind, name
        )
        kind = _DEFAULT_KIND
    entry: EnvReq = {
        "name": name,
        "kind": kind,
        "purpose": _join_middle(segs),
        "used_by": _strip_scope_prefix(segs[-1], "used by"),
    }
    return entry, warning


def parse_prerequisites(verification_md_text) -> PrereqSpec:
    """Parse the ``## Prerequisites`` block of verification.md text into a
    ``PrereqSpec``.

    Never raises and never reads a file. An absent block, a non-string input,
    or an empty string yields the empty spec ``{"tools": [], "env": [],
    "warnings": [], "block_present": False}`` — so at runtime a project with no
    declaration behaves exactly as it did before this feature (DEC-3,
    non-breaking).

    ``block_present`` is True iff a ``## Prerequisites`` heading was found. It
    distinguishes a deliberate ``none`` declaration (present, empty) from an
    absent/omitted block — a contract violation under the conditionally-required
    authoring rule, which PREREQ-3 surfaces as a non-blocking warning. Malformed
    lines are skipped and warned; value-shaped payloads are stripped and warned;
    only names, types, and purposes are ever stored.
    """
    spec: PrereqSpec = {"tools": [], "env": [], "warnings": [], "block_present": False}
    if not isinstance(verification_md_text, str) or not verification_md_text:
        return spec

    lines = verification_md_text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if _PREREQ_H2_RE.match(line):
            start = idx + 1
            break
    if start is None:
        return spec
    spec["block_present"] = True  # the (required) section was emitted

    current = None  # "tools" | "env" | None (outside a recognized subsection)
    for line in lines[start:]:
        if _NEXT_H2_RE.match(line):
            break  # the next ## section ends the Prerequisites block
        if _TOOLS_H3_RE.match(line):
            current = "tools"
            continue
        if _ENV_H3_RE.match(line):
            current = "env"
            continue
        if _NEXT_H3_RE.match(line):
            current = None  # an unrecognized ### subsection — ignore its bullets
            continue
        if current is None:
            continue  # prose/bullets before any recognized subsection
        m = _BULLET_RE.match(line)
        if not m:
            continue  # blank line or prose inside a subsection
        body = m.group(1).strip()
        if current == "tools":
            entry, warning = _parse_tool_line(body)
            if entry is not None:
                spec["tools"].append(entry)
        else:
            entry, warning = _parse_env_line(body)
            if entry is not None:
                spec["env"].append(entry)
        if warning:
            spec["warnings"].append(warning)
    return spec
