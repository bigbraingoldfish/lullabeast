"""Where a project in Lullabeast's container is on the reader's own computer.

In the container, projects live in the folder docker-compose.yml bind-mounts at
/data/projects (the image names it in AUTODEV_PROJECTS_MOUNT). The person reading a
completion report sees them in the host folder mounted there: deploy/projects in
their clone, by default. Docker Desktop on Windows names that folder in the
container's mount table. Anywhere else HOST_PROJECTS_DIR, set in deploy/.env, names
it, and without it the folder can only be described.
"""

import os
import posixpath
import re

MOUNTINFO = "/proc/self/mountinfo"

_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")
_DRIVE_ROOT = re.compile(r"[A-Za-z]:\\")
_DRVFS = re.compile(r"(?:^|[,;])aname=drvfs(?:[,;]|$)")


def projects_mount(environ=None):
    """The container's projects folder, or None outside the container."""
    environ = os.environ if environ is None else environ
    return (environ.get("AUTODEV_PROJECTS_MOUNT") or "").strip() or None


def _unescape(field):
    # mountinfo writes a space, tab, newline or backslash in a field as \040, \011, \012, \134.
    return _OCTAL_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), field)


def mounted_from(mount_point, mountinfo=None):
    """The host folder mounted at ``mount_point``, when the mount table names it.

    Docker Desktop on Windows (WSL 2) shares a drive over 9p: the mount's source is
    the drive (``C:\\``) and its root the rest of the path. Other hosts' bind mounts
    give a path relative to the filesystem it lives on, which needn't be the path
    you'd type, so they return None, as does a missing or unreadable table.
    """
    try:
        with open(MOUNTINFO if mountinfo is None else mountinfo, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    found = None
    for line in lines:
        # id parent major:minor root mount-point options [optional...] - fstype source superopts
        fields = line.split(" ")
        if len(fields) < 10 or _unescape(fields[4]) != mount_point or "-" not in fields[6:]:
            continue
        sep = fields.index("-", 6)
        if len(fields) < sep + 4:
            continue
        fstype, source, superopts = fields[sep + 1], _unescape(fields[sep + 2]), fields[sep + 3]
        found = None  # a later mount at the same point hides an earlier one
        if fstype == "9p" and _DRVFS.search(superopts) and _DRIVE_ROOT.fullmatch(source):
            found = source + _unescape(fields[3]).lstrip("/").replace("/", "\\")
    return found


def host_projects_dir(environ=None, mountinfo=None):
    """The host folder behind the projects mount: HOST_PROJECTS_DIR when it is set,
    else what the mount table names. None outside the container or when unknown."""
    environ = os.environ if environ is None else environ
    mount = projects_mount(environ)
    if not mount:
        return None
    override = (environ.get("HOST_PROJECTS_DIR") or "").strip()
    return override or mounted_from(mount, mountinfo)


def is_windows_path(path):
    return bool(re.match(r"[A-Za-z]:[\\/]|\\\\", path))


def join(root, *parts):
    """``parts`` (written with ``/``) under ``root``, in ``root``'s own separator."""
    sep = "\\" if is_windows_path(root) else "/"
    return sep.join([root.rstrip("\\/"), *(p.replace("/", sep) for p in parts)])


def host_path(container_path, environ=None, mountinfo=None):
    """``container_path`` as the reader's computer names it. None outside the
    container, outside the projects mount, or when the host folder is unknown."""
    environ = os.environ if environ is None else environ
    mount = projects_mount(environ)
    root = host_projects_dir(environ, mountinfo)
    if not mount or not root:
        return None
    rel = posixpath.relpath(container_path, mount)
    if rel == ".." or rel.startswith("../"):
        return None
    return root if rel == "." else join(root, rel)


def compose_dir(host_projects_root):
    """The host folder docker-compose.yml is in, when the projects folder is its
    default ``./projects`` mount (``deploy/projects`` in a clone); else None."""
    sep = "\\" if is_windows_path(host_projects_root) else "/"
    parts = host_projects_root.rstrip("\\/").split(sep)
    if len(parts) >= 3 and parts[-1] == "projects" and parts[-2] == "deploy":
        return sep.join(parts[:-1])
    return None
