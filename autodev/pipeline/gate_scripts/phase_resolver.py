import os
import re
import json
import sys

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
    for i, line in enumerate(lines):
        line = line.strip()
        
        # Match checkbox patterns like `- [ ] `ID` | RISK | Goal`
        match = re.match(r'- \[( |x|-|!)\] `([^`]+)` \| ([^\|]+) \| (.+)', line)
        if match:
            status_char, phase_id, risk, goal = match.groups()
            
            # Capture the global index for this phase before deciding whether to
            # skip it — so completed/skipped phases still advance the counter.
            phase_number = global_idx
            global_idx += 1

            if status_char == 'x' or status_char == '-':
                continue # Skip completed or skipped phases
                
            parts = phase_id.split('-')
            status = 'BLOCKED' if status_char == '!' else 'PENDING'
            
            category = parts[0] if len(parts) > 1 else 'GENERAL'
            
            current_phase = {
                "phase_number": phase_number,
                "detail": f"Phase {phase_id}: {goal.strip()}",
                "category": category,
                "exit_criteria": [],
                "status": status,
                "raw_id": phase_id
            }
            
            # Scrape subsequent > lines for test/notes
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line.startswith('>'):
                    current_phase["exit_criteria"].append(next_line[1:].strip())
                elif next_line and not next_line.startswith('>'):
                    break
                j += 1
            break # Stop at first incomplete/blocked phase
            
    return current_phase

def validate_and_identify(roadmap_path=None):
    if not roadmap_path:
        pipeline_project = os.path.expanduser("~/.openclaw/pipeline-project")
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
        
    phase = parse_roadmap(roadmap_path)
    if not phase:
        print("PIPELINE_COMPLETE")
        sys.exit(0)
        
    # Write current_phase.json
    out_path = os.path.join(os.path.dirname(roadmap_path), "current_phase.json")
    # For testing, write to current dir if pipeline project doesn't exist
    if not os.path.exists(os.path.dirname(out_path)):
        out_path = "current_phase.json"
        
    try:
        import tempfile
        fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(out_path), prefix="current_phase_")
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
