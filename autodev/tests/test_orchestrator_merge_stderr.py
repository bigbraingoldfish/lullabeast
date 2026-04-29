"""Fix 3: When `git merge` fails during the post-reviewer git-ops block, the
escalation reason must include the actual git stderr — not the static
"Merge conflict on Phase N" string.

A non-zero merge exit can mean: a real merge conflict, a missing branch
(phase/N not created), a dirty working tree, or any other git failure. The
hardcoded "Merge conflict on Phase N" mislabels every one of those as a
conflict, which sends the operator and Signal notification down the wrong
investigation path.

Static check: confirm that merge_result.stderr is referenced after the
merge_result.returncode check in orchestrator.py.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORCHESTRATOR_PATH = os.path.join(REPO_ROOT, "autodev", "pipeline", "orchestrator.py")


def test_merge_failure_uses_stderr_for_escalation_reason():
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()

    # Locate the merge-failure block: starts at "merge_result.returncode != 0".
    idx = source.find("merge_result.returncode != 0")
    assert idx != -1, "Could not locate merge_result.returncode check in orchestrator.py"

    # Look at the next ~600 chars (the failure block, before the next git step).
    block = source[idx:idx + 800]

    assert "merge_result.stderr" in block, (
        "The merge-failure block must reference merge_result.stderr so the "
        "actual git error (missing branch, dirty tree, real conflict, etc.) "
        "appears in escalation_trigger_reason and Signal notifications."
    )

    # Guard against the old hardcoded string remaining as the *only* signal —
    # if it appears, it must appear as a fallback after stderr extraction,
    # not as the unconditional transition_state argument.
    transition_pattern = re.search(
        r'transition_state\(\s*"RUNNING"\s*,\s*f?"Merge conflict on Phase \{phase\}"\s*\)',
        block,
    )
    assert transition_pattern is None, (
        "transition_state must not be called with the static "
        '"Merge conflict on Phase {phase}" string. Pass the captured git '
        "stderr (with a fallback for empty stderr) so the real cause surfaces."
    )
