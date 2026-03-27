import os
import sys
import glob

def check_repo_init():
    home = os.path.expanduser("~")
    pipeline_project = os.path.join(home, ".openclaw", "pipeline-project")
    
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
        agent_dir = os.path.join(home, ".openclaw", f"workspace-{agent}")
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
