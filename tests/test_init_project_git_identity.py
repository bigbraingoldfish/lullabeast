"""Git-identity fallback at project init (`_ensure_repo_git_identity`).

The pipeline commits inside project repos (init commits, phase merges, the
executor agent's own commits). On a fresh machine with no global git identity
those commits fail mid-pipeline, far from the cause. install.sh now fails fast
on a missing global identity; these tests pin the second, hermetic layer — the
server sets a repo-local fallback identity at init time when none resolves,
and never overrides a resolvable one.

The fixture isolates the test from the host machine's real git identity via
GIT_CONFIG_GLOBAL / GIT_CONFIG_NOSYSTEM (git >= 2.32), so these tests pass —
and stay meaningful — whether or not the developer box has ~/.gitconfig.
"""
import subprocess
from unittest.mock import patch

import pytest

from ui.server import (
    _ensure_repo_git_identity,
    _GIT_FALLBACK_IDENTITY_NAME,
    _GIT_FALLBACK_IDENTITY_EMAIL,
    _run_init_project,
)

# _run_init_project reads the real load_config() internally; sandbox the
# machine's deployment profile (public vs dev stack) so the suite passes on
# either — see the fixture docstring in tests/conftest.py.
pytestmark = pytest.mark.usefixtures("hermetic_deploy_profile")

# Self-contained seeds (path-fixture rule: no machine paths, no cross-test imports).
VALID_ROADMAP_SEED = (
    "- [ ] `TEST-E1` | LOW | Do the thing\n"
    "  > Test: It works.\n"
    "  **Behavioral Verification:**\n"
    "  - **User-observable:** The user sees the thing happen.\n"
    "  - **How we'll check:** Run the thing; confirm output.\n"
    "  - **If this fails, the user sees:** Nothing happens.\n"
)

VALID_VERIFICATION_CONTENT = (
    "# Verification\n\n"
    "## Project type\n"
    "cli\n\n"
    "## Entry point\n"
    "- Command: `mycli --help`\n"
    "- Ready signal: process exits 0\n\n"
    "## Public surface\n"
    "1. Do the thing\n\n"
    "## Verification stack\n"
    "- Acceptance tool: subprocess + assertions\n"
)


@pytest.fixture
def no_git_identity(monkeypatch, tmp_path):
    """Point git's global config at an empty file so no identity resolves.

    Returns the config path so a test can write an identity into it to model
    the configured-machine case.
    """
    gitconfig = tmp_path / "isolated-gitconfig"
    gitconfig.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # Env-var identities would let commits succeed without any config,
    # masking exactly the failure mode under test.
    for var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "EMAIL",
    ):
        monkeypatch.delenv(var, raising=False)
    return gitconfig


def _bare_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    return path


def _config_value(repo, key, scope=None):
    cmd = ["git", "-C", str(repo), "config"]
    if scope:
        cmd.append(scope)
    cmd.append(key)
    return subprocess.run(cmd, capture_output=True, text=True)


def test_helper_sets_fallback_identity_when_none_resolves(no_git_identity, tmp_path):
    repo = _bare_repo(tmp_path / "repo")
    _ensure_repo_git_identity(str(repo))
    assert _config_value(repo, "user.name").stdout.strip() == _GIT_FALLBACK_IDENTITY_NAME
    assert _config_value(repo, "user.email").stdout.strip() == _GIT_FALLBACK_IDENTITY_EMAIL


def test_helper_never_overrides_resolvable_identity(no_git_identity, tmp_path):
    no_git_identity.write_text(
        "[user]\n\tname = Real Person\n\temail = real@example.com\n"
    )
    repo = _bare_repo(tmp_path / "repo")
    _ensure_repo_git_identity(str(repo))
    # No repo-local override written — global identity keeps resolving.
    assert _config_value(repo, "user.name", "--local").returncode != 0
    assert _config_value(repo, "user.email", "--local").returncode != 0
    assert _config_value(repo, "user.email").stdout.strip() == "real@example.com"


def test_mode_a_init_succeeds_without_global_identity(no_git_identity, tmp_path):
    """Mode A (fresh repo) used to die at its initial commit on identity-less
    machines; the repo-local fallback makes it hermetic."""
    repo = tmp_path / "myproject"
    with patch("ui.server._atomic_symlink_swap"):
        result = _run_init_project(
            str(repo), VALID_ROADMAP_SEED, verification_content=VALID_VERIFICATION_CONTENT
        )
    assert result["ok"] is True, result.get("error")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
    )
    assert head.returncode == 0, "initial commit must exist"
    assert _config_value(repo, "user.email").stdout.strip() == _GIT_FALLBACK_IDENTITY_EMAIL


def test_mode_b_init_succeeds_without_global_identity(no_git_identity, tmp_path):
    """Mode B (existing repo) commits new pipeline structure; same fallback."""
    repo = _bare_repo(tmp_path / "myproject")
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "main"], capture_output=True)
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", "user.name=Seed", "-c", "user.email=seed@example.com",
            "commit", "-m", "initial",
        ],
        check=True, capture_output=True,
    )
    with patch("ui.server._atomic_symlink_swap"):
        result = _run_init_project(
            str(repo), VALID_ROADMAP_SEED, verification_content=VALID_VERIFICATION_CONTENT
        )
    assert result["ok"] is True, result.get("error")
    assert _config_value(repo, "user.email").stdout.strip() == _GIT_FALLBACK_IDENTITY_EMAIL
