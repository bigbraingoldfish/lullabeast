import os
import sys
import json
import subprocess
import tempfile

# Share env resolution with the orchestrator and other gate scripts.
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from env_resolvers import resolve_pipeline_root  # noqa: E402


def _derive_pipeline_project() -> str:
    repo_path = os.environ.get(
        "AUTODEV_REPO_PATH",
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        ),
    )
    return os.path.join(resolve_pipeline_root(repo_path), "pipeline-project")


def init_phase():
    workspace = _derive_pipeline_project() + os.sep
    if not os.path.exists(workspace):
        print(f"[ERROR] Derived pipeline project path does not exist: {workspace!r}")
        print("[FATAL] Cannot fall back to CWD — this would write phase metadata "
              "to the wrong project tree. Set AUTODEV_REPO_PATH, OPENCLAW_ROOT, "
              "or AUTODEV_PIPELINE_ROOT to point at the correct installation.")
        sys.exit(1)

    # Same layout as utils.ARTIFACTS_DIR — derived from workspace so tests can patch
    # _derive_pipeline_project without stale import-time paths.
    artifacts_dir = os.path.join(workspace.rstrip(os.sep), ".autodev", "pipeline") + os.sep
        
    # 1. Initialize phase_state.json under .autodev/pipeline/ (matches orchestrator).
    os.makedirs(artifacts_dir, exist_ok=True)
    state_file = os.path.join(artifacts_dir, "phase_state.json")
    initial_state = {
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        "reviewer_rejected": False
    }
    
    try:
        fd, temp_path = tempfile.mkstemp(dir=artifacts_dir.rstrip(os.sep), prefix="phase_state_")
        with os.fdopen(fd, 'w') as f:
            json.dump(initial_state, f, indent=2)
        os.replace(temp_path, state_file)
        print("[INFO] Initialized phase_state.json")
    except Exception as e:
        print(f"[ERROR] Failed to init phase_state.json: {e}")
        sys.exit(1)
        
    # 2. Get Phase ID to create git branch
    current_phase_file = os.path.join(artifacts_dir, "current_phase.json")
    phase_id = "unknown"
    if os.path.exists(current_phase_file):
        with open(current_phase_file, 'r') as f:
            data = json.load(f)
            phase_id = data.get("raw_id", str(data.get("phase_number", "0")))
            
    # 3. Create/checkout git branch
    # Note: In real execution, cwd needs to be the project directory
    try:
        branch_name = f"phase/{phase_id}"
        # git checkout branch 2>/dev/null || git checkout -b branch
        # Need to run in the workspace directory (which is a symlink to the repo)
        project_dir = os.path.realpath(workspace)
        if os.path.exists(os.path.join(project_dir, ".git")):
            cmd = f"git checkout {branch_name} 2>/dev/null || git checkout -b {branch_name}"
            subprocess.run(cmd, shell=True, cwd=project_dir, check=True)
            print(f"[INFO] Checked out branch: {branch_name}")
        else:
            print("[WARN] Not a git repository, skipping branch creation")
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Subprocess failed: {e}")
        # Non-fatal for the init script itself during testing, but should be noted
        
if __name__ == "__main__":
    init_phase()
