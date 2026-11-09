"""The completion review: what the reviewer is told, and what happens to what it writes.

Shared by the orchestrator's end-of-run review (``_run_completion_review``) and the
dashboard's on-demand trigger (``POST /api/completion-review/{project}``), so the
two prompts can't drift and both commit the same files.
"""

import os
import subprocess
import time

# What the reviewer writes at the project root, and all its commit takes.
COMPLETION_DOCS = ("README.md", "CHANGELOG.md", "completion_report.md")
COMMIT_SUBJECT = "docs: completion review"
COMMIT_BODY = "README.md, CHANGELOG.md and completion_report.md, written by the completion reviewer."


def completion_message(project_abs_path: str, artifacts_rel: str = "pipeline-project/.autodev/pipeline") -> str:
    """The reviewer's instructions for the documentation pass."""
    return (
        f"Begin completion documentation. Read the project source and git diff to understand "
        f"what was built. Produce three artifacts at the project root: README.md updates, "
        f"a CHANGELOG.md entry, and completion_report.md.\n\n"
        f"completion_report.md must walk a non-technical user through running the project "
        f"from a fresh terminal with no prior context — assume they have not opened a shell "
        f"yet and are not in any particular directory. Structure it as:\n"
        f"  1. What was built (one short paragraph).\n"
        f"  2. How to run it — write this as numbered steps. Step 1 must be: "
        f"'Open Terminal (macOS/Linux) or PowerShell (Windows)'. Step 2 must be the command "
        f"`cd {project_abs_path}` in its own fenced ``` code block (use the literal absolute "
        f"path shown — never substitute a generic placeholder for the real path). "
        f"Then one fenced code block per command: install dependencies, build, run, test. "
        f"Reference the actual scripts present in package.json / Makefile / pyproject.toml / "
        f"etc. — only commands a user can paste verbatim.\n"
        f"  3. Files changed (brief list).\n"
        f"  4. Suggested next steps (2–4 bullets).\n\n"
        f"Every shell command must live in its own ``` fenced code block so the UI can render "
        f"one Copy button per command. Do not group multiple commands in one block.\n\n"
        f"Then write {artifacts_rel}/reviewer_output.done."
    )


def commit_completion_docs(project_dir: str) -> bool:
    """Commit the completion docs the reviewer wrote, and nothing else.

    The orchestrator commits everything else a run changes (each phase lands as a
    commit and a merge), so leaving these out left a finished project with
    uncommitted files, which Launch and the queue then refuse to start on.
    Best-effort: True when a commit was made.
    """
    names = [n for n in COMPLETION_DOCS if os.path.isfile(os.path.join(project_dir, n))]
    if not names:
        return False

    def git(*args):
        return subprocess.run(["git", "-C", project_dir, *args], capture_output=True, text=True)

    for name in names:
        git("add", "--", name)  # one at a time: a path a .gitignore excludes fails alone
    staged = git("diff", "--cached", "--name-only", "--", *names).stdout.split()
    if not staged:
        return False
    result = git("commit", "-q", "-m", COMMIT_SUBJECT, "-m", COMMIT_BODY, "--", *staged)
    return result.returncode == 0


def commit_completion_docs_when_done(
    project_dir: str,
    sentinel_path: str,
    session_key: str,
    started_at: float,
    timeout_seconds: int = 300,
) -> bool:
    """Wait for the reviewer's sentinel, then commit its docs.

    For the dashboard's on-demand trigger, which returns before the reviewer
    finishes; it runs this on a background thread. A review that never finishes
    leaves its files uncommitted, as the orchestrator's own review does.
    """
    from sentinel_poller import poll_for_sentinel

    if not poll_for_sentinel(
        sentinel_path=sentinel_path,
        timeout_seconds=timeout_seconds,
        min_sentinel_mtime=started_at,
        expected_session_key=session_key,
    ):
        return False
    return commit_completion_docs(project_dir)


def on_demand_session_key(project: str) -> str:
    """A fresh session per on-demand review: a reattached one would resume the last
    review's transcript, on the model that session was created with."""
    return f"pipeline:completion:{project}:reviewer-t{int(time.time())}"
