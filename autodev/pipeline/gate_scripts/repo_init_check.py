import os
import sys
import glob

# Share env resolution with the orchestrator and other gate scripts.
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from env_resolvers import resolve_openclaw_root  # noqa: E402
from utils import _derive_runtime_root  # noqa: E402


def _openclaw_root() -> str:
    """OpenClaw install root (workspaces, openclaw.json, pipeline-project symlink).

    Wraps :func:`env_resolvers.resolve_openclaw_root`. Canonical env var is
    ``OPENCLAW_ROOT``. Docker or bind-mounted installs set ``OPENCLAW_ROOT`` to
    that path (e.g. /home/user/project/.openclaw) instead of ``~/.openclaw``.
    """
    return os.path.abspath(resolve_openclaw_root())


def check_repo_init():
    oc_root = _openclaw_root()
    # Match orchestrator / phase_resolver: repo-local `.autodev/pipeline-project`
    # by default. Operators who want state next to OpenClaw set
    # AUTODEV_PIPELINE_ROOT to the OpenClaw root explicitly.
    runtime_root = os.path.abspath(os.path.expanduser(_derive_runtime_root()))
    pipeline_project = os.path.join(runtime_root, "pipeline-project")

    if not os.path.exists(pipeline_project):
        print(f"[ERROR] Shared workspace symlink not found: {pipeline_project}")
        sys.exit(1)
        
    roadmap_files = []
    for ext in ['*.md', '*.yaml', '*.json']:
        roadmap_files.extend(glob.glob(os.path.join(pipeline_project, f"*oadmap{ext}")))
        roadmap_files.extend(glob.glob(os.path.join(pipeline_project, f"*Roadmap{ext}")))
        
    if not roadmap_files:
        print(f"[ERROR] No roadmap file found in {pipeline_project}")
        sys.exit(1)
        
    agents = ["planner", "executor", "reviewer", "escalation"]
    required_docs = ["AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"]
    
    for agent in agents:
        agent_dir = os.path.join(oc_root, f"workspace-{agent}")
        if not os.path.exists(agent_dir):
            print(f"[ERROR] Agent workspace not found: {agent_dir}")
            sys.exit(1)
        for doc in required_docs:
            doc_path = os.path.join(agent_dir, doc)
            if not os.path.exists(doc_path):
                print(f"[ERROR] Required support doc missing: {doc_path}")
                sys.exit(1)
                
    gitignore_path = os.path.join(pipeline_project, ".gitignore")
    if not os.path.exists(gitignore_path):
        print(f"[ERROR] .gitignore file missing in project root: {pipeline_project}")
        sys.exit(1)

    # Ensure all pipeline metadata files are excluded from git tracking.
    # These files are orchestrator-managed per-turn state and must never be committed —
    # git tracking causes reset_phase/reset_execution to overwrite them with stale data
    # from prior phases. Auto-inject any missing entries rather than hard-failing, so
    # existing projects without the entries are self-healed on first pipeline startup.
    required_pipeline_gitignore = [
        "*.done",
        "phase_state.json",
        "planner_output.json",
        "executor_output.json",
        "reviewer_output.json",
        "escalation_output.json",
        "current_phase.json",
        "failure_context.json",
    ]
    required_python_tool_gitignore = [
        "__pycache__/",
        "*.py[cod]",
        ".pytest_cache/",
        ".ruff_cache/",
    ]
    with open(gitignore_path, "r") as f:
        gitignore_content = f.read()
    existing_lines = set(line.strip() for line in gitignore_content.splitlines())
    missing_pipeline = [e for e in required_pipeline_gitignore if e not in existing_lines]
    missing_toolcache = [e for e in required_python_tool_gitignore if e not in existing_lines]
    if missing_pipeline:
        header = "\n# Pipeline metadata — orchestrator-managed per-turn state, never committed\n"
        append_block = header + "\n".join(missing_pipeline) + "\n"
        with open(gitignore_path, "a") as f:
            f.write(append_block)
        for entry in missing_pipeline:
            print(f"[WARN] repo_init_check: injected missing .gitignore entry: {entry}")
    if missing_toolcache:
        header2 = "\n# Python bytecode and tool caches — never committed\n"
        append_block2 = header2 + "\n".join(missing_toolcache) + "\n"
        with open(gitignore_path, "a") as f:
            f.write(append_block2)
        for entry in missing_toolcache:
            print(f"[WARN] repo_init_check: injected missing .gitignore entry: {entry}")

    print("[INFO] Repo initialization check passed.")
    sys.exit(0)

if __name__ == "__main__":
    check_repo_init()
