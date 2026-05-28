import os
import re
import json
import sys

# Share env resolution with the orchestrator and other gate scripts.
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from env_resolvers import resolve_pipeline_root  # noqa: E402


# Stage D — structured per-phase block extraction (P0 §2.3).
_PHASE_HEADER_RE = re.compile(r'^- \[( |x|-|!)\] `([^`]+)` \| ([^\|]+) \| (.+)$')
_BV_BLOCK_HEADER_RE = re.compile(r'^\s*\*\*Behavioral Verification:\*\*\s*$')
_BV_SUBBULLET_RES = {
    "user_observable": re.compile(r"^\s*-\s+\*\*User-observable:\*\*\s+(.+)$"),
    "how_to_check": re.compile(r"^\s*-\s+\*\*How we'll check:\*\*\s+(.+)$"),
    "failure_language": re.compile(r"^\s*-\s+\*\*If this fails, the user sees:\*\*\s+(.+)$"),
}
_SECTION_HEADERS = {
    "entry_criteria": re.compile(r'^\s*\*\*Entry Criteria:\*\*\s*$'),
    "exit_criteria_block": re.compile(r'^\s*\*\*Exit Criteria:\*\*\s*$'),
    "tdd_requirements": re.compile(r'^\s*\*\*TDD Requirements:\*\*\s*$'),
    "done_criteria": re.compile(r'^\s*\*\*Done Criteria:\*\*\s*$'),
    "behavioral_verification": _BV_BLOCK_HEADER_RE,
}
_TDD_LINE_RE = re.compile(r'^\s*-\s+`([^`]+)`\s*:\s*(.+)$')
_DONE_LINE_RE = re.compile(r'^\s*-\s+\[\s?\]\s+(.+)$')


def _extract_behavioral_verification(body_lines):
    """Return ``{user_observable, how_to_check, failure_language}`` or ``None`` if no block present."""
    block_start = None
    for idx, line in enumerate(body_lines):
        if _BV_BLOCK_HEADER_RE.match(line):
            block_start = idx + 1
            break
    if block_start is None:
        return None
    captured = {"user_observable": None, "how_to_check": None, "failure_language": None}
    for line in body_lines[block_start:]:
        # Stop scanning the block when we hit another structured header or a new phase.
        if any(pat.match(line) for key, pat in _SECTION_HEADERS.items() if key != "behavioral_verification"):
            break
        if _PHASE_HEADER_RE.match(line.rstrip()):
            break
        for key, pat in _BV_SUBBULLET_RES.items():
            m = pat.match(line)
            if m:
                captured[key] = m.group(1).strip()
    if all(v is not None for v in captured.values()):
        return captured
    # Partial block — treat as absent to keep the orchestrator's gating
    # contract clean. Preflight is the gate that enforces completeness.
    return None


def _slice_until_next_section(body_lines, start_idx):
    """Return lines from start_idx up to (exclusive of) the next structured header or phase header."""
    end = len(body_lines)
    for j in range(start_idx, len(body_lines)):
        line = body_lines[j]
        if _PHASE_HEADER_RE.match(line.rstrip()):
            return body_lines[start_idx:j], j
        if any(pat.match(line) for pat in _SECTION_HEADERS.values()):
            return body_lines[start_idx:j], j
    return body_lines[start_idx:end], end


def _extract_section_block(body_lines, header_re):
    """Return the body text after a section header, or empty string when absent."""
    for idx, line in enumerate(body_lines):
        if header_re.match(line):
            sliced, _ = _slice_until_next_section(body_lines, idx + 1)
            return "\n".join(s.strip() for s in sliced if s.strip()).strip()
    return ""


def _extract_tdd_requirements(body_lines):
    for idx, line in enumerate(body_lines):
        if _SECTION_HEADERS["tdd_requirements"].match(line):
            sliced, _ = _slice_until_next_section(body_lines, idx + 1)
            out = []
            for entry in sliced:
                m = _TDD_LINE_RE.match(entry)
                if m:
                    out.append({"file": m.group(1).strip(), "description": m.group(2).strip()})
            return out
    return []


def _extract_done_criteria(body_lines):
    for idx, line in enumerate(body_lines):
        if _SECTION_HEADERS["done_criteria"].match(line):
            sliced, _ = _slice_until_next_section(body_lines, idx + 1)
            out = []
            for entry in sliced:
                m = _DONE_LINE_RE.match(entry)
                if m:
                    out.append(m.group(1).strip())
            return out
    return []


def parse_roadmap(filepath):
    with open(filepath, 'r') as f:
        lines = f.readlines()

    current_phase = None
    # global_idx tracks the 0-based sequential position of every matched phase line
    # in the full roadmap (including completed and skipped phases).  Using a global
    # sequential index — rather than the local numeric suffix from the phase ID —
    # ensures session keys are unique even when multiple subsystems share the same
    # trailing integer (e.g. INFRA-1, CORE-1, and UI-1 all have suffix "1" but
    # occupy different positions in the roadmap and therefore produce distinct keys).
    global_idx = 0
    # P1 Stage D: track the most recent COMPLETED phase as we walk so the
    # reviewer can re-execute its how_to_check recipe alongside the current
    # phase's (N→N-1 regression check). Only `x`-status phases qualify;
    # `-` (skipped) and `!` (escalated) intentionally do not update this.
    # N→N-1 only — if N-1 has no how_to_check, we do not walk further back
    # to N-2. Promotion to full iteration is P3 Stage B.
    last_completed = None
    for i, line in enumerate(lines):
        raw_line = line.rstrip("\n")
        stripped = line.strip()

        match = _PHASE_HEADER_RE.match(stripped)
        if not match:
            continue

        status_char, phase_id, risk, goal = match.groups()

        # Capture the global index for this phase before deciding whether to
        # skip it — so completed/skipped phases still advance the counter.
        phase_number = global_idx
        global_idx += 1

        if status_char == 'x':
            # Completed phase: extract its behavioural how_to_check (if any)
            # for the regression check that fires on the current phase.
            _completed_body_end = len(lines)
            for j in range(i + 1, len(lines)):
                if _PHASE_HEADER_RE.match(lines[j].strip()):
                    _completed_body_end = j
                    break
            _completed_body = [ln.rstrip("\n") for ln in lines[i + 1:_completed_body_end]]
            _completed_bv = _extract_behavioral_verification(_completed_body)
            last_completed = {
                "raw_id": phase_id,
                "how_to_check": _completed_bv.get("how_to_check") if _completed_bv else None,
            }
            continue
        if status_char == '-':
            # Skipped phase: never landed; do NOT update last_completed.
            continue

        parts = phase_id.split('-')
        status = 'BLOCKED' if status_char == '!' else 'PENDING'

        category = parts[0] if len(parts) > 1 else 'GENERAL'

        current_phase = {
            "phase_number": phase_number,
            "detail": f"Phase {phase_id}: {goal.strip()}",
            "category": category,
            "exit_criteria": [],
            "status": status,
            "raw_id": phase_id,
            "behavioral_verification": None,
            "entry_criteria": "",
            "exit_criteria_block": "",
            "tdd_requirements": [],
            "done_criteria": [],
            # P1 Stage D: most recent completed phase's id + how_to_check
            # recipe. Both None when there is no completed predecessor.
            # how_to_check is None when the predecessor lacks a behavioural
            # block — the reviewer's regression branch is then skipped.
            "prior_phase_raw_id": last_completed["raw_id"] if last_completed else None,
            "prior_phase_how_to_check": last_completed["how_to_check"] if last_completed else None,
        }

        # Bounded scan: lines from the phase header until the next phase header
        # or EOF — the full body of THIS phase.
        body_end = len(lines)
        for j in range(i + 1, len(lines)):
            if _PHASE_HEADER_RE.match(lines[j].strip()):
                body_end = j
                break
        body_lines = [ln.rstrip("\n") for ln in lines[i + 1:body_end]]

        # Pre-existing `> ...` scrape for exit_criteria list (kept for back-compat).
        # Stop at the first non-blank, non-`>` line — preserves the historical
        # behavior used by reviewer-gate consumers.
        for entry in body_lines:
            stripped_entry = entry.strip()
            if stripped_entry.startswith('>'):
                current_phase["exit_criteria"].append(stripped_entry[1:].strip())
            elif stripped_entry:
                break

        # Structured per-block extraction (Stage D additive).
        current_phase["behavioral_verification"] = _extract_behavioral_verification(body_lines)
        current_phase["entry_criteria"] = _extract_section_block(
            body_lines, _SECTION_HEADERS["entry_criteria"]
        )
        current_phase["exit_criteria_block"] = _extract_section_block(
            body_lines, _SECTION_HEADERS["exit_criteria_block"]
        )
        current_phase["tdd_requirements"] = _extract_tdd_requirements(body_lines)
        current_phase["done_criteria"] = _extract_done_criteria(body_lines)
        break  # Stop at first incomplete/blocked phase

    return current_phase

def _derive_pipeline_project() -> str:
    """Resolve pipeline-project path using the same runtime-root logic as the orchestrator."""
    repo_path = os.environ.get(
        "AUTODEV_REPO_PATH",
        # gate_scripts/ → pipeline/ → autodev/ → repo root: 4 dirname calls
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        ),
    )
    return os.path.join(resolve_pipeline_root(repo_path), "pipeline-project")


def validate_and_identify(roadmap_path=None):
    if not roadmap_path:
        pipeline_project = _derive_pipeline_project()
        # Find roadmap file
        import glob
        for ext in ['*.md', '*.yaml', '*.json']:
            for pattern in [f"*oadmap{ext}", f"*Roadmap{ext}"]:
                matches = glob.glob(os.path.join(pipeline_project, pattern))
                if matches:
                    roadmap_path = matches[0]
                    break
            if roadmap_path: break
            
    if not roadmap_path or not os.path.exists(roadmap_path):
        print("[ERROR] Roadmap file not found.")
        sys.exit(1)

    _rp = os.path.expanduser(str(roadmap_path))
    if not os.path.isabs(_rp):
        print("[ERROR] Roadmap path must be absolute (no CWD-relative output for current_phase.json).")
        sys.exit(1)

    phase = parse_roadmap(_rp)
    if not phase:
        print("PIPELINE_COMPLETE")
        sys.exit(0)
        
    # Write current_phase.json under .autodev/pipeline/ in the project root (roadmap dir).
    project_root = os.path.dirname(os.path.abspath(_rp))
    out_dir = os.path.join(project_root, ".autodev", "pipeline")
    out_path = os.path.join(out_dir, "current_phase.json")
    if not project_root or not os.path.isdir(project_root):
        print(f"[ERROR] Cannot determine output directory for current_phase.json "
              f"(roadmap_path={roadmap_path!r}). "
              "Pass an absolute roadmap path so the output directory is unambiguous.")
        sys.exit(1)
        
    # Stage D: point agents at the project-level verification.md.
    phase["verification_path"] = os.path.join(project_root, "verification.md")

    try:
        import tempfile
        os.makedirs(out_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=out_dir, prefix="current_phase_")
        with os.fdopen(fd, 'w') as f:
            # exclude status and raw_id from the spec's output schema, but we need status to halt orchestrator
            # Actually, spec says Write "current_phase.json" – phase detail, category, exit_criteria
            # We'll just dump the whole object, the extra fields are fine.
            json.dump(phase, f, indent=2)
        os.replace(temp_path, out_path)
    except Exception as e:
        print(f"[ERROR] Failed to write current_phase.json: {e}")
        sys.exit(1)
        
    if phase["status"] == "BLOCKED":
        print(f"BLOCKED: Phase {phase['raw_id']} is blocked.")
        sys.exit(2)
        
    print(f"PENDING: Phase {phase['raw_id']} identified.")
    sys.exit(0)

if __name__ == "__main__":
    validate_and_identify(sys.argv[1] if len(sys.argv) > 1 else None)
