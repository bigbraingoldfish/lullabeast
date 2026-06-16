"""Structured merge failure diagnosis written to phase_state.json on Phase 10 failure.

When `git merge phase/CORE-N` fails (e.g. branch was deleted after RESET_PHASE and
the commit landed on main instead), the orchestrator must write a structured diagnosis
block to phase_state.json before escalating.  This gives the escalation agent concrete
facts — branch name, whether it exists, last-good commit, current HEAD — rather than
only the raw git stderr string.

Uses AST analysis to verify the code structure, since Phase 10 is deeply embedded in
the main loop and not easily callable in isolation.
"""

import ast
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
ORCHESTRATOR_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")

for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse():
    with open(ORCHESTRATOR_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    return source, ast.parse(source)


def _find_merge_failure_block(tree: ast.AST):
    """Locate the if-block guarded by merge_result.returncode != 0.

    Returns the list of AST nodes inside that block, or [] if not found.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Looking for:  merge_result.returncode != 0
        if not (isinstance(test, ast.Compare) and isinstance(test.ops[0], ast.NotEq)):
            continue
        left = test.left
        if not (
            isinstance(left, ast.Attribute)
            and left.attr == "returncode"
            and isinstance(left.value, ast.Name)
            and "merge" in left.value.id.lower()
        ):
            continue
        comps = test.comparators
        if not (len(comps) == 1 and isinstance(comps[0], ast.Constant) and comps[0].value == 0):
            continue
        return node.body
    return []


def _string_literals_in_nodes(nodes):
    """Collect all string constant values from AST nodes."""
    literals = set()
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                literals.add(child.value)
    return literals


def _call_names_in_nodes(nodes):
    """Collect all function/method names called within the given nodes."""
    names = set()
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Attribute):
                    names.add(func.attr)
                elif isinstance(func, ast.Name):
                    names.add(func.id)
    return names


def _referenced_names_in_nodes(nodes):
    """Collect all bare-name identifiers referenced within the given nodes.

    Since LAUNCH-7 the error codes are module constants imported from
    ``error_codes`` (e.g. ``last_error_code = ERR_MERGE_FAILED``) rather than
    inline string literals, so a code now shows up as an ``ast.Name`` here
    instead of an ``ast.Constant`` in :func:`_string_literals_in_nodes`.
    """
    names = set()
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                names.add(child.id)
    return names


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMergeFailureDiagnosis:

    def test_merge_failure_block_sets_err_merge_failed(self):
        """merge_result.returncode != 0 block must set last_error_code to ERR_MERGE_FAILED.

        The escalation agent reads phase_state.json to understand why it was triggered.
        ERR_MERGE_FAILED is a known error code that distinguishes a git topology failure
        from a reviewer gate failure or executor timeout.
        """
        _, tree = _parse()
        nodes = _find_merge_failure_block(tree)
        assert nodes, (
            "Could not locate the 'if merge_result.returncode != 0' block in orchestrator.py. "
            "Ensure Phase 10 merge failure handling is present."
        )
        # Accept either an inline string literal or a reference to the
        # error_codes.ERR_MERGE_FAILED constant (LAUNCH-7 centralized the codes).
        literals = _string_literals_in_nodes(nodes)
        names = _referenced_names_in_nodes(nodes)
        assert "ERR_MERGE_FAILED" in literals or "ERR_MERGE_FAILED" in names, (
            "The merge failure block does not set last_error_code to ERR_MERGE_FAILED "
            "(neither a string literal nor a reference to the error_codes.ERR_MERGE_FAILED "
            "constant). Set last_error_code inside the merge failure handler."
        )

    def test_merge_failure_block_records_branch_name(self):
        """Diagnosis must include the branch name that failed to merge.

        The escalation agent needs the exact branch name to understand the topology
        (e.g. 'phase/CORE-E4') rather than guessing from pipeline_state.json.
        """
        _, tree = _parse()
        nodes = _find_merge_failure_block(tree)
        assert nodes, "merge failure block not found"
        literals = _string_literals_in_nodes(nodes)
        assert "merge_failure_branch" in literals, (
            "The merge failure block does not write 'merge_failure_branch' to phase_state. "
            "Include the branch name in the diagnosis dict passed to write_phase_state_atomic."
        )

    def test_merge_failure_block_records_last_good_commit(self):
        """Diagnosis must include the last-good commit (phase_base_commit).

        This lets the escalation agent (and any future recovery logic) know exactly
        which commit to rewind to if a hard reset is needed.
        """
        _, tree = _parse()
        nodes = _find_merge_failure_block(tree)
        assert nodes, "merge failure block not found"
        literals = _string_literals_in_nodes(nodes)
        assert "merge_failure_last_good_commit" in literals, (
            "The merge failure block does not write 'merge_failure_last_good_commit' to "
            "phase_state. Include phase_base_commit from orchestrator state in the diagnosis."
        )

    def test_merge_failure_block_records_branch_existence(self):
        """Diagnosis must check and record whether the phase branch actually exists.

        'not something we can merge' is opaque. Knowing branch_exists=False immediately
        tells the escalation agent that the branch was deleted (likely by reset_phase)
        rather than that there was a genuine merge conflict.
        """
        _, tree = _parse()
        nodes = _find_merge_failure_block(tree)
        assert nodes, "merge failure block not found"
        literals = _string_literals_in_nodes(nodes)
        assert "merge_failure_branch_exists" in literals, (
            "The merge failure block does not write 'merge_failure_branch_exists' to "
            "phase_state. Run git show-ref inside the failure handler and record the result."
        )

    def test_merge_failure_block_calls_write_phase_state_atomic(self):
        """Diagnosis must be persisted via write_phase_state_atomic before escalating.

        If the orchestrator crashes between writing phase_state and transitioning to
        escalation, crash recovery re-reads phase_state — the diagnosis must already
        be there. write_phase_state_atomic uses mkstemp+os.replace for atomicity.
        """
        _, tree = _parse()
        nodes = _find_merge_failure_block(tree)
        assert nodes, "merge failure block not found"
        call_names = _call_names_in_nodes(nodes)
        assert "write_phase_state_atomic" in call_names, (
            "The merge failure block does not call write_phase_state_atomic. "
            "Persist the diagnosis dict atomically before transitioning to escalation."
        )

    def test_merge_failure_block_records_current_head(self):
        """Diagnosis must capture the current HEAD commit at failure time.

        If commits landed on the wrong branch (e.g. main), HEAD at failure time
        identifies exactly where those commits are so recovery can cherry-pick or reset.
        """
        _, tree = _parse()
        nodes = _find_merge_failure_block(tree)
        assert nodes, "merge failure block not found"
        literals = _string_literals_in_nodes(nodes)
        assert "merge_failure_head_commit" in literals, (
            "The merge failure block does not record 'merge_failure_head_commit'. "
            "Run git rev-parse HEAD inside the handler and include it in the diagnosis."
        )
