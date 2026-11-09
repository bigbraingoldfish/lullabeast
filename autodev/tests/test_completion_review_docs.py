"""What happens to what the completion reviewer writes.

The review writes README.md updates, a CHANGELOG.md entry and completion_report.md
at the project root after the last phase lands. Nothing committed them, so a
finished project was left with uncommitted files, and Launch and the queue refuse
to start on a repo with uncommitted work. The orchestrator now commits them after
its review, and the dashboard's on-demand review does once its reviewer finishes.
"""

import os
import subprocess
import sys
import time

PIPELINE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "autodev", "pipeline",
)
if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)

import completion_review as cr  # noqa: E402

_ENV = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=_ENV).stdout


def _repo(tmp_path):
    """A finished project: one commit, and an identity of its own for the commit
    under test (the container sets a global one)."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("# Proj\n")
    (repo / "index.html").write_text("<p>hi</p>\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _committed(repo) -> set:
    return set(_git(repo, "show", "--name-only", "--format=", "HEAD").split())


class TestCommitCompletionDocs:
    def test_commits_the_three_docs_and_nothing_else(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "README.md").write_text("# Proj\n\nHow to run it.\n")
        (repo / "CHANGELOG.md").write_text("# Changelog\n")
        (repo / "completion_report.md").write_text("# Completion report\n")
        (repo / "scratch.txt").write_text("not the reviewer's\n")

        assert cr.commit_completion_docs(str(repo)) is True
        assert _git(repo, "log", "-1", "--format=%s").strip() == "docs: completion review"
        assert _committed(repo) == {"README.md", "CHANGELOG.md", "completion_report.md"}
        assert _git(repo, "status", "--porcelain").strip() == "?? scratch.txt"

    def test_leaves_other_staged_work_staged(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "index.html").write_text("<p>edited</p>\n")
        _git(repo, "add", "index.html")
        (repo / "completion_report.md").write_text("# Completion report\n")

        assert cr.commit_completion_docs(str(repo)) is True
        assert _committed(repo) == {"completion_report.md"}
        assert _git(repo, "status", "--porcelain").strip() == "M  index.html"

    def test_nothing_written_nothing_committed(self, tmp_path):
        repo = _repo(tmp_path)
        head = _git(repo, "rev-parse", "HEAD")
        assert cr.commit_completion_docs(str(repo)) is False
        assert _git(repo, "rev-parse", "HEAD") == head

    def test_an_ignored_doc_does_not_block_the_others(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / ".gitignore").write_text("completion_report.md\n")
        _git(repo, "add", ".gitignore")
        _git(repo, "commit", "-q", "-m", "ignore the report")
        (repo / "completion_report.md").write_text("# Completion report\n")
        (repo / "CHANGELOG.md").write_text("# Changelog\n")

        assert cr.commit_completion_docs(str(repo)) is True
        assert _committed(repo) == {"CHANGELOG.md"}


class TestCommitWhenDone:
    """The on-demand trigger returns before its reviewer finishes; a background
    thread waits for the reviewer's sentinel, then commits."""

    def test_commits_once_the_reviewers_sentinel_lands(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "completion_report.md").write_text("# Completion report\n")
        key = "pipeline:completion:proj:reviewer-t1"
        sentinel = tmp_path / "reviewer_output.done"
        started = time.time() - 1
        sentinel.write_text(key + "\n")

        assert cr.commit_completion_docs_when_done(str(repo), str(sentinel), key, started,
                                                   timeout_seconds=5) is True
        assert "completion_report.md" in _committed(repo)

    def test_no_sentinel_no_commit(self, tmp_path):
        repo = _repo(tmp_path)
        (repo / "completion_report.md").write_text("# Completion report\n")
        head = _git(repo, "rev-parse", "HEAD")
        assert cr.commit_completion_docs_when_done(str(repo), str(tmp_path / "missing.done"),
                                                   "k", time.time(), timeout_seconds=1) is False
        assert _git(repo, "rev-parse", "HEAD") == head


def test_each_on_demand_review_gets_its_own_session(monkeypatch):
    stamps = iter([1000.0, 1001.0])
    monkeypatch.setattr(cr.time, "time", lambda: next(stamps))
    first, second = cr.on_demand_session_key("proj"), cr.on_demand_session_key("proj")
    assert first != second
    assert first.startswith("pipeline:completion:proj:reviewer")
