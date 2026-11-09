"""Finding where the container's projects folder is on the reader's computer."""

import os
import sys

PIPELINE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "autodev", "pipeline",
)
if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)

import host_paths  # noqa: E402

# The /data/projects line from a real Docker Desktop on Windows (WSL 2) container.
DOCKER_DESKTOP = (
    "2932 2931 0:86 /Users/Z/Desktop/Lullabeast/refresh/smoke/projects /data/projects "
    "rw,noatime - 9p C:\\134 rw,dirsync,aname=drvfs;path=C:\\;uid=0;gid=0;metadata;"
    "symlinkroot=/mnt/host/,mmap,access=client,msize=65536,trans=fd,rfd=5,wfd=5"
)
DATA_VOLUME = (
    "2931 2891 8:32 /data/docker/volumes/x_lullabeast-data/_data /data rw,relatime "
    "master:62 - ext4 /dev/sdc rw"
)
LINUX_BIND = "2932 2931 8:1 /home/jane/lullabeast/deploy/projects /data/projects rw,relatime - ext4 /dev/sda1 rw"
IN_CONTAINER = {"AUTODEV_PROJECTS_MOUNT": "/data/projects"}


def _table(tmp_path, *lines):
    path = tmp_path / "mountinfo"
    path.write_text("".join(line + "\n" for line in lines))
    return str(path)


class TestMountedFrom:
    def test_reads_docker_desktop_on_windows(self, tmp_path):
        table = _table(tmp_path, DATA_VOLUME, DOCKER_DESKTOP)
        assert host_paths.mounted_from("/data/projects", table) == (
            r"C:\Users\Z\Desktop\Lullabeast\refresh\smoke\projects"
        )

    def test_unescapes_a_space_in_the_path(self, tmp_path):
        line = DOCKER_DESKTOP.replace("/Users/Z/Desktop", "/Users/Jane\\040Doe/Desktop")
        assert host_paths.mounted_from("/data/projects", _table(tmp_path, line)) == (
            r"C:\Users\Jane Doe\Desktop\Lullabeast\refresh\smoke\projects"
        )

    def test_reads_the_drive_the_folder_is_on(self, tmp_path):
        line = DOCKER_DESKTOP.replace("C:\\134", "D:\\134").replace("path=C:\\", "path=D:\\")
        assert host_paths.mounted_from("/data/projects", _table(tmp_path, line)).startswith("D:\\Users")

    def test_a_linux_bind_mount_is_not_trusted(self, tmp_path):
        # Its root is relative to the filesystem holding it, not necessarily a path you'd type.
        assert host_paths.mounted_from("/data/projects", _table(tmp_path, LINUX_BIND)) is None

    def test_the_last_mount_at_the_path_wins(self, tmp_path):
        assert host_paths.mounted_from("/data/projects", _table(tmp_path, DOCKER_DESKTOP, LINUX_BIND)) is None
        assert host_paths.mounted_from("/data/projects", _table(tmp_path, LINUX_BIND, DOCKER_DESKTOP))

    def test_no_mount_or_no_table_is_unknown(self, tmp_path):
        assert host_paths.mounted_from("/data/projects", _table(tmp_path, DATA_VOLUME)) is None
        assert host_paths.mounted_from("/data/projects", str(tmp_path / "missing")) is None


class TestHostProjectsDir:
    def test_unknown_outside_the_container(self, tmp_path):
        assert host_paths.host_projects_dir({}, _table(tmp_path, DOCKER_DESKTOP)) is None

    def test_host_projects_dir_wins_over_the_mount_table(self, tmp_path):
        env = {**IN_CONTAINER, "HOST_PROJECTS_DIR": "/home/jane/lullabeast/deploy/projects"}
        assert host_paths.host_projects_dir(env, _table(tmp_path, DOCKER_DESKTOP)) == (
            "/home/jane/lullabeast/deploy/projects"
        )

    def test_falls_back_to_the_mount_table(self, tmp_path):
        assert host_paths.host_projects_dir(IN_CONTAINER, _table(tmp_path, DOCKER_DESKTOP)).startswith(
            "C:\\Users\\Z"
        )


class TestHostPath:
    def test_a_project_in_the_mount_gets_its_host_path(self, tmp_path):
        table = _table(tmp_path, DOCKER_DESKTOP)
        assert host_paths.host_path("/data/projects/first-run-snake", IN_CONTAINER, table) == (
            r"C:\Users\Z\Desktop\Lullabeast\refresh\smoke\projects\first-run-snake"
        )

    def test_joins_in_the_hosts_own_separator(self, tmp_path):
        env = {**IN_CONTAINER, "HOST_PROJECTS_DIR": "/home/jane/projects/"}
        assert host_paths.host_path("/data/projects/a/b", env, _table(tmp_path)) == "/home/jane/projects/a/b"

    def test_a_project_outside_the_mount_has_none(self, tmp_path):
        table = _table(tmp_path, DOCKER_DESKTOP)
        assert host_paths.host_path("/data/elsewhere/proj", IN_CONTAINER, table) is None
        assert host_paths.host_path("/data/projects-old/proj", IN_CONTAINER, table) is None


class TestComposeDir:
    def test_the_default_mount_is_next_to_docker_compose_yml(self):
        assert host_paths.compose_dir(r"C:\Users\Jane\lullabeast\deploy\projects") == (
            r"C:\Users\Jane\lullabeast\deploy"
        )
        assert host_paths.compose_dir("/home/jane/lullabeast/deploy/projects/") == "/home/jane/lullabeast/deploy"

    def test_a_folder_mounted_from_elsewhere_has_none(self):
        assert host_paths.compose_dir(r"C:\Users\Z\Desktop\Lullabeast\refresh\smoke\projects") is None
        assert host_paths.compose_dir("/srv/projects") is None
