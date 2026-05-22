"""Parser for roadmap.md checkbox format."""
import re
from pathlib import Path
from typing import Dict, List, Optional


_BV_BLOCK_HEADER_RE = re.compile(r'^\s*\*\*Behavioral Verification:\*\*\s*$')
_BV_USER_RE = re.compile(r"^\s*-\s+\*\*User-observable:\*\*\s+(.+)$")
_BV_HOW_RE = re.compile(r"^\s*-\s+\*\*How we'll check:\*\*\s+(.+)$")
_BV_FAIL_RE = re.compile(r"^\s*-\s+\*\*If this fails, the user sees:\*\*\s+(.+)$")


def _finalize_behavioral_verification(buf: Dict[str, Optional[str]]) -> Optional[Dict[str, str]]:
    """Return the BV dict only when all three sub-bullets were captured, else None.

    Partial blocks are treated as absent so the UI parser surfaces the same
    null-vs-complete contract as ``phase_resolver.parse_roadmap`` — preflight
    is the gate that enforces completeness.
    """
    if buf["user_observable"] and buf["how_to_check"] and buf["failure_language"]:
        return {
            "user_observable": buf["user_observable"],
            "how_to_check": buf["how_to_check"],
            "failure_language": buf["failure_language"],
        }
    return None


def parse_roadmap(roadmap_path: str) -> List[Dict]:
    """Parse a roadmap.md file and return structured phase data.

    Per phase the returned dict carries:

    - ``id`` (str): the phase identifier (e.g. ``CORE-E1``)
    - ``goal`` (str): the description text after the priority column
    - ``status`` (str): one of ``pending`` / ``complete`` / ``skipped`` / ``blocked``
    - ``exit_criteria`` (list[str]): bodies of ``> ...`` lines
    - ``behavioral_verification`` (dict | None): the structured
      ``User-observable`` / ``How we'll check`` / ``If this fails, the user sees``
      block (Stage D). ``None`` when the block is absent or incomplete —
      a transitional case for pre-P0 roadmaps; preflight refuses to stage
      a project whose roadmap is missing the block.
    """
    path = Path(roadmap_path)

    if not path.exists():
        return []

    try:
        content = path.read_text()
    except Exception:
        return []

    if not content.strip():
        return []

    lines = content.split('\n')
    phases: List[Dict] = []
    current_phase: Optional[Dict] = None
    bv_buf: Optional[Dict[str, Optional[str]]] = None

    phase_pattern = re.compile(
        r'^-\s*\[([ x!\-])\]\s*`([^`]+)`\s*\|\s*\w+\s*\|\s*(.+)$'
    )
    exit_criteria_pattern = re.compile(r'^\s*>\s*\w+:\s*(.+)$')

    def _flush_current():
        nonlocal current_phase, bv_buf
        if current_phase is not None:
            current_phase["behavioral_verification"] = _finalize_behavioral_verification(
                bv_buf or {"user_observable": None, "how_to_check": None, "failure_language": None}
            )
            phases.append(current_phase)
        current_phase = None
        bv_buf = None

    for line in lines:
        # `>` exit-criteria lines belong to the active phase.
        exit_match = exit_criteria_pattern.match(line)
        if exit_match and current_phase is not None:
            current_phase["exit_criteria"].append(exit_match.group(1))
            continue

        phase_match = phase_pattern.match(line)
        if phase_match:
            _flush_current()

            checkbox = phase_match.group(1).strip()
            if checkbox == 'x':
                status = 'complete'
            elif checkbox == ' ':
                status = 'pending'
            elif checkbox == '-':
                status = 'skipped'
            elif checkbox == '!':
                status = 'blocked'
            else:
                status = 'pending'

            current_phase = {
                "id": phase_match.group(2).strip(),
                "goal": phase_match.group(3).strip(),
                "status": status,
                "exit_criteria": [],
                "behavioral_verification": None,
            }
            bv_buf = {"user_observable": None, "how_to_check": None, "failure_language": None}
            continue

        # Stage D — Behavioral Verification sub-bullet capture for the active phase.
        if current_phase is not None and bv_buf is not None:
            m = _BV_USER_RE.match(line)
            if m:
                bv_buf["user_observable"] = m.group(1).strip()
                continue
            m = _BV_HOW_RE.match(line)
            if m:
                bv_buf["how_to_check"] = m.group(1).strip()
                continue
            m = _BV_FAIL_RE.match(line)
            if m:
                bv_buf["failure_language"] = m.group(1).strip()
                continue

    _flush_current()
    return phases
