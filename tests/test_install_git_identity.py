"""Static lint: install.sh must fail fast on a missing global git identity.

The pipeline makes git commits in project repos, so user.name/user.email are a
hard install prerequisite. Without this check a fresh machine sails through
install and breaks the first time the executor (or project init) tries to
commit — a confusing mid-pipeline failure instead of an obvious setup error.
Mirrors the static-lint posture of tests/test_infra3_systemd_unit.py.
"""
import os
import re

INSTALL_SH = "install.sh"


def _content():
    with open(INSTALL_SH) as f:
        return f.read()


def test_install_sh_exists():
    assert os.path.exists(INSTALL_SH)


def test_probes_both_global_identity_keys():
    content = _content()
    assert re.search(r"git config --global user\.name", content), (
        "install.sh must probe `git config --global user.name`"
    )
    assert re.search(r"git config --global user\.email", content), (
        "install.sh must probe `git config --global user.email`"
    )


def test_fails_fast_when_identity_missing():
    """The missing-identity branch must terminate the install (fail), not warn."""
    content = _content()
    m = re.search(
        r'if \[ -z "\$GIT_ID_NAME" \] \|\| \[ -z "\$GIT_ID_EMAIL" \];'
        r" then(.*?)\bfi\b",
        content,
        re.DOTALL,
    )
    assert m, "install.sh must branch on empty GIT_ID_NAME/GIT_ID_EMAIL"
    assert re.search(r"\bfail\b", m.group(1)), (
        "missing git identity must fail fast, not silently continue"
    )


def test_failure_message_names_exact_fix_commands():
    """The operator must be able to paste the fix verbatim."""
    content = _content()
    assert 'git config --global user.name \\"Your Name\\"' in content or (
        'git config --global user.name "Your Name"' in content
    )
    assert 'git config --global user.email \\"you@example.com\\"' in content or (
        'git config --global user.email "you@example.com"' in content
    )


def test_identity_check_precedes_dependency_install():
    """Prerequisite checks come before any state-changing install step."""
    content = _content()
    check_pos = content.index("GIT_ID_NAME=")
    deps_pos = content.index("PYTHON DEPENDENCIES")
    assert check_pos < deps_pos, (
        "git identity check must run before the dependency-install step"
    )
