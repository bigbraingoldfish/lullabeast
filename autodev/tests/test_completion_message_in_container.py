"""A completion report is read on the reader's computer, not in the container.

In Lullabeast's container the reviewer wrote its run steps from where it was: cd to
the container's /data/projects path, which doesn't exist on the reader's computer,
then install Node packages and Playwright's browser that only the container had
already. The reviewer is now told where the project is on the reader's computer
and to run what needs the container's tools through docker compose exec.
"""

import os
import sys

import pytest

PIPELINE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "autodev", "pipeline",
)
if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)

import completion_review as cr  # noqa: E402
import host_paths  # noqa: E402

# Docker Desktop on Windows, with the default ./projects mount of a clone.
DOCKER_DESKTOP = (
    "2932 2931 0:86 /Users/Jane\\040Doe/lullabeast/deploy/projects /data/projects "
    "rw,noatime - 9p C:\\134 rw,dirsync,aname=drvfs;path=C:\\;uid=0;gid=0;metadata,"
    "mmap,access=client,msize=65536,trans=fd,rfd=5,wfd=5\n"
)
LINUX_BIND = "2932 2931 8:1 /home/jane/lullabeast/deploy/projects /data/projects rw - ext4 /dev/sda1 rw\n"
NO_GUESSING = "Use only the paths given here, as given: never build or guess another."
COMPOSE_IN_WORDS = "said in words: its path isn't known here"


@pytest.fixture
def mounts(monkeypatch, tmp_path):
    """Inside the image, with a mount table the test writes."""
    monkeypatch.setenv("AUTODEV_PROJECTS_MOUNT", "/data/projects")
    table = tmp_path / "mountinfo"
    table.write_text("")
    monkeypatch.setattr(host_paths, "MOUNTINFO", str(table))
    return table


def test_names_the_project_folder_on_the_readers_computer(mounts):
    mounts.write_text(DOCKER_DESKTOP)
    msg = cr.completion_message("/data/projects/snake")

    assert r"there the project is at C:\Users\Jane Doe\lullabeast\deploy\projects\snake." in msg
    assert r"C:\Users\Jane Doe\lullabeast\deploy\projects\snake\index.html" in msg
    assert "'Open PowerShell'" in msg
    assert r'`cd "C:\Users\Jane Doe\lullabeast\deploy"`' in msg
    assert 'docker compose exec lullabeast bash -c "cd /data/projects/snake && npm test"' in msg
    assert "Never tell them to cd to /data/projects/snake outside a docker compose exec command" in msg
    assert "or to install anything on their computer for the tests: they run in the container" in msg
    assert NO_GUESSING in msg


def test_playwright_tests_install_their_browser_in_the_container_first(mounts):
    # A project's Playwright can need a newer Chromium than the image ships. The
    # executor downloads it into the container during the build, and recreating the
    # container drops it, so a report's test command alone failed after an upgrade.
    msg = cr.completion_message("/data/projects/snake")

    assert "start the test command with `npx playwright install chromium && `" in msg
    assert "lasts only until the container is recreated" in msg


def test_a_folder_mounted_from_elsewhere_gets_the_compose_step_in_words(mounts):
    # A live review given only "the folder they start Lullabeast from" wrote a cd to
    # a deploy folder it made up next to the projects folder.
    mounts.write_text(DOCKER_DESKTOP.replace("/lullabeast/deploy/projects", "/work/projects"))
    msg = cr.completion_message("/data/projects/snake")

    assert r"there the project is at C:\Users\Jane Doe\work\projects\snake." in msg
    assert "'Open PowerShell'" in msg
    assert COMPOSE_IN_WORDS in msg
    assert NO_GUESSING in msg
    assert r'cd "C:\Users\Jane Doe\work"' not in msg


def test_without_a_host_path_it_describes_the_folder(mounts):
    mounts.write_text(LINUX_BIND)
    msg = cr.completion_message("/data/projects/snake")

    assert "deploy/projects/snake in their Lullabeast clone" in msg
    assert "/home/jane" not in msg
    assert "'Open Terminal (macOS/Linux) or PowerShell (Windows)'" in msg
    assert COMPOSE_IN_WORDS in msg
    assert NO_GUESSING in msg


def test_host_projects_dir_names_the_folder_on_any_host(mounts, monkeypatch):
    monkeypatch.setenv("HOST_PROJECTS_DIR", "/home/jane/lullabeast/deploy/projects")
    msg = cr.completion_message("/data/projects/snake")

    assert "there the project is at /home/jane/lullabeast/deploy/projects/snake." in msg
    assert "'Open Terminal'" in msg
    assert '`cd "/home/jane/lullabeast/deploy"`' in msg
    assert '`cd "/home/jane/lullabeast/deploy/projects/snake"`' in msg


def test_outside_the_container_the_walkthrough_is_unchanged(monkeypatch):
    monkeypatch.delenv("AUTODEV_PROJECTS_MOUNT", raising=False)
    msg = cr.completion_message("/home/jane/snake")

    assert "'Open Terminal (macOS/Linux) or PowerShell (Windows)'" in msg
    assert "`cd /home/jane/snake`" in msg
    assert "docker compose" not in msg
    assert "Where the reader is" not in msg


def test_the_readme_gets_no_absolute_paths_anywhere(mounts, monkeypatch):
    rule = "README.md is read wherever the project is cloned, so it names no absolute path"
    assert rule in cr.completion_message("/data/projects/snake")
    monkeypatch.delenv("AUTODEV_PROJECTS_MOUNT")
    assert rule in cr.completion_message("/home/jane/snake")
