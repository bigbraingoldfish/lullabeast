"""Parser for roadmap.md checkbox format."""
import re
from pathlib import Path
from typing import List, Dict


def parse_roadmap(roadmap_path: str) -> List[Dict]:
    """
    Parse a roadmap.md file and return structured phase data.
    
    Args:
        roadmap_path: Path to the roadmap.md file
        
    Returns:
        List of phase dicts with keys: id, goal, status, exit_criteria
    """
    path = Path(roadmap_path)
    
    # Handle absent or empty file
    if not path.exists():
        return []
    
    try:
        content = path.read_text()
    except Exception:
        return []
    
    if not content.strip():
        return []
    
    lines = content.split('\n')
    phases = []
    current_phase = None
    
    # Regex patterns
    # Match phase lines like: - [x] `PHASE-1` | LOW | Goal text
    phase_pattern = re.compile(
        r'^-\s*\[([ x!\-])\]\s*`([^`]+)`\s*\|\s*\w+\s*\|\s*(.+)$'
    )
    
    # Match exit criteria lines like: > Test: something or > Notes: something
    # Strip the prefix (Test:, Notes:, etc.) to get just the criteria text
    exit_criteria_pattern = re.compile(r'^\s*>\s*\w+:\s*(.+)$')
    
    for line in lines:
        # Check for exit criteria line (must follow a phase)
        exit_match = exit_criteria_pattern.match(line)
        if exit_match and current_phase is not None:
            current_phase["exit_criteria"].append(exit_match.group(1))
            continue
        
        # Check for phase line
        phase_match = phase_pattern.match(line)
        if phase_match:
            # Save previous phase if exists
            if current_phase is not None:
                phases.append(current_phase)
            
            # Parse checkbox status
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
            
            # Create new phase
            current_phase = {
                "id": phase_match.group(2).strip(),
                "goal": phase_match.group(3).strip(),
                "status": status,
                "exit_criteria": []
            }
            continue
        
        # Non-phase line - if we have a current phase being built, 
        # we need to handle the case where exit criteria might follow on next lines
        # But for now, if it's not a phase or exit criteria, we just continue
    
    # Don't forget to add the last phase
    if current_phase is not None:
        phases.append(current_phase)
    
    return phases
