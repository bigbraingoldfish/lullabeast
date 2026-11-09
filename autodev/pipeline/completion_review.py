"""The completion review: what the reviewer is told, and what happens to what it writes.

Shared by the orchestrator's end-of-run review (``_run_completion_review``) and the
dashboard's on-demand trigger (``POST /api/completion-review/{project}``), so the
two prompts can't drift and both commit the same files.
"""

import os
import posixpath
import subprocess
import time

import host_paths

# What the reviewer writes at the project root, and all its commit takes.
COMPLETION_DOCS = ("README.md", "CHANGELOG.md", "completion_report.md")
COMMIT_SUBJECT = "docs: completion review"
COMMIT_BODY = "README.md, CHANGELOG.md and completion_report.md, written by the completion reviewer."


def completion_message(project_abs_path: str, artifacts_rel: str = "pipeline-project/.autodev/pipeline") -> str:
    """The reviewer's instructions for the documentation pass.

    In Lullabeast's container the report's run steps are for the reader's own
    computer, where the container's paths don't exist and its tools may be missing.
    """
    if host_paths.projects_mount():
        where, how_to_run = _run_steps_from_the_host(project_abs_path)
    else:
        where = ""
        how_to_run = (
            "  2. How to run it — write this as numbered steps. Step 1 must be: "
            "'Open Terminal (macOS/Linux) or PowerShell (Windows)'. Step 2 must be the command "
            f"`cd {project_abs_path}` in its own fenced ``` code block (use the literal absolute "
            "path shown — never substitute a generic placeholder for the real path). "
            "Then one fenced code block per command: install dependencies, build, run, test. "
            "Reference the actual scripts present in package.json / Makefile / pyproject.toml / "
            "etc. — only commands a user can paste verbatim.\n"
        )
    return (
        "Begin completion documentation. Read the project source and git diff to understand "
        "what was built. Produce three artifacts at the project root: README.md updates, "
        "a CHANGELOG.md entry, and completion_report.md.\n\n"
        f"{where}"
        "completion_report.md must walk a non-technical user through running the project "
        "from a fresh terminal with no prior context — assume they have not opened a shell "
        "yet and are not in any particular directory. Structure it as:\n"
        "  1. What was built (one short paragraph).\n"
        f"{how_to_run}"
        "  3. Files changed (brief list).\n"
        "  4. Suggested next steps (2–4 bullets).\n\n"
        "README.md is read wherever the project is cloned, so it names no absolute path: "
        "write every path relative to the project root.\n\n"
        "Every shell command must live in its own ``` fenced code block so the UI can render "
        "one Copy button per command. Do not group multiple commands in one block.\n\n"
        f"Then write {artifacts_rel}/reviewer_output.done."
    )


def _run_steps_from_the_host(container_path: str):
    """Where a project in the container is for the reader, and how they run it from there."""
    name = posixpath.basename(container_path.rstrip("/"))
    host = host_paths.host_path(container_path)
    if host:
        place = f"at {host}"
        page = f"the page's file by its full path there (such as {host_paths.join(host, 'index.html')})"
        shell = "PowerShell" if host_paths.is_windows_path(host) else "Terminal"
        host_step = f'the command `cd "{host}"` in its own fenced block'
        compose = host_paths.compose_dir(host_paths.host_projects_dir())
    else:
        in_clone = f"deploy/projects/{name}"
        place = (f"the {name} folder in the projects folder next to Lullabeast's "
                 f"docker-compose.yml ({in_clone} in their Lullabeast clone)")
        page = f"the page's file in that folder (such as {in_clone}/index.html in their Lullabeast clone)"
        shell = "Terminal (macOS/Linux) or PowerShell (Windows)"
        host_step = f"a step to go to {in_clone} in their Lullabeast clone"
        compose = None
    if compose:
        compose_step = f'the command `cd "{compose}"` in its own fenced ``` code block'
    else:
        compose_step = ("to go to the folder they start Lullabeast from with docker compose "
                        "(deploy in their Lullabeast clone, where docker-compose.yml is), said in "
                        "words: its path isn't known here")
    where = (
        "Where the reader is: you are inside Lullabeast's Docker container, where this project "
        f"is at {container_path}. The reader opens completion_report.md on their own computer, "
        f"where that path does not exist; there the project is {place}. This container has "
        "Node.js, Python and Playwright's Chromium, and the project's installed dependencies; "
        "their computer may have none of them. Use only the paths given here, as given: never "
        "build or guess another.\n\n"
    )
    how_to_run = (
        "  2. How to run it — numbered steps, by what the project is:\n"
        "     - A web page that works opened straight from disk (plain HTML, CSS and scripts: "
        "no build step, no server, no type=\"module\" scripts): tell them to open "
        f"{page} in their browser, the path in its own fenced ``` code block.\n"
        "     - Tests, builds and other commands run in the container, where everything is "
        f"installed. Step 1 must be: 'Open {shell}'. Step 2 must be {compose_step}. Then one "
        "fenced code block per command, each written like "
        f"`docker compose exec lullabeast bash -c \"cd {container_path} && npm test\"` with the "
        "project's real command, from package.json / Makefile / pyproject.toml / etc., in place "
        "of npm test — only commands a user can paste verbatim.\n"
        "     - Anything that serves on a port (a dev server, an API) or opens a window can't be "
        "used from inside the container, which publishes only Lullabeast's own ports and has no "
        "display. Tell them to run it on their computer: name the runtime it needs installed "
        f"there, then {host_step}, then each install and run command in its own fenced block.\n"
        f"     Never tell them to cd to {container_path} outside a docker compose exec command, "
        "or to install anything on their computer for the tests: they run in the container. If "
        "the tests use Playwright, start the test command with `npx playwright install chromium "
        "&& `: the image ships one Chromium build, and a newer one the project's Playwright "
        "downloads lasts only until the container is recreated (the install is quick when the "
        "build is already there).\n"
    )
    return where, how_to_run


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
