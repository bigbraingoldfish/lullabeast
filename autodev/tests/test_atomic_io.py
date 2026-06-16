"""Unit tests for ``autodev/pipeline/atomic_io.py`` — the canonical atomic writer.

LAUNCH-5 consolidated ~37 ad-hoc ``mkstemp``+``os.replace`` sites onto
``write_json_atomic`` / ``write_text_atomic``. These tests pin the shared contract:
a *unique* temp in the destination's directory, an ``os.replace`` commit, the temp
removed on failure (never a stranded ``*.tmp``), and the ``raise_on_error`` switch
that lets callers keep their original error policy (re-raise vs swallow).

Assertion style mirrors ``test_phase5_session_cleanup.py`` (spy ``os.replace``;
assert no leftover ``*.tmp``) and the temp-cleanup checks in
``test_escalation_summary_wait_before_advance.py``.
"""

import glob
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import atomic_io  # noqa: E402 - sys.path wired by conftest


class TestWriteJsonAtomic:
    def test_writes_valid_json_str_path(self, tmp_path):
        dest = str(tmp_path / "state.json")
        assert atomic_io.write_json_atomic(dest, {"a": 1}) is True
        with open(dest) as f:
            assert json.load(f) == {"a": 1}

    def test_writes_valid_json_path_object(self, tmp_path):
        dest = tmp_path / "state.json"
        assert atomic_io.write_json_atomic(dest, {"b": 2}) is True
        assert json.loads(dest.read_text()) == {"b": 2}

    def test_indent_two_is_default(self, tmp_path):
        dest = tmp_path / "s.json"
        atomic_io.write_json_atomic(dest, {"a": 1, "b": 2})
        # Byte-identical to the most common call form, json.dump(data, f, indent=2).
        assert dest.read_text() == json.dumps({"a": 1, "b": 2}, indent=2)

    def test_indent_none_is_compact(self, tmp_path):
        dest = tmp_path / "s.json"
        atomic_io.write_json_atomic(dest, {"a": 1, "b": 2}, indent=None)
        assert dest.read_text() == json.dumps({"a": 1, "b": 2})

    def test_overwrites_existing_atomically(self, tmp_path):
        dest = tmp_path / "s.json"
        dest.write_text('{"old": true}')
        atomic_io.write_json_atomic(dest, {"new": True})
        assert json.loads(dest.read_text()) == {"new": True}

    def test_commit_via_os_replace_no_leftover_temp(self, tmp_path):
        dest = tmp_path / "s.json"
        real_replace = os.replace
        replaced = []

        def spy(src, dst):
            replaced.append(dst)
            return real_replace(src, dst)

        with patch("atomic_io.os.replace", side_effect=spy):
            atomic_io.write_json_atomic(dest, {"a": 1})

        assert any(str(dst).endswith("s.json") for dst in replaced)  # atomic rename
        assert not glob.glob(str(tmp_path / "*.tmp"))                # no leftover temp

    def test_unique_temp_per_write_no_collision(self, tmp_path):
        # Two writes to the SAME destination must use distinct temp files, so two
        # concurrent writers cannot truncate each other's temp — the corruption
        # bug LAUNCH-5 fixes in server.py's fixed-".tmp" sites.
        dest = tmp_path / "s.json"
        srcs = []
        real_replace = os.replace

        def spy(src, dst):
            srcs.append(src)
            return real_replace(src, dst)

        with patch("atomic_io.os.replace", side_effect=spy):
            atomic_io.write_json_atomic(dest, {"a": 1})
            atomic_io.write_json_atomic(dest, {"a": 2})

        assert len(srcs) == 2
        assert srcs[0] != srcs[1]  # unique temp names


class TestErrorPolicy:
    def test_raise_on_error_true_reraises_and_cleans_temp(self, tmp_path):
        dest = tmp_path / "s.json"
        with pytest.raises(TypeError):
            atomic_io.write_json_atomic(dest, {"bad": {1, 2, 3}})  # set not serializable
        assert not dest.exists()
        assert os.listdir(tmp_path) == []  # temp removed, nothing stranded

    def test_raise_on_error_false_returns_false_and_cleans_temp(self, tmp_path):
        dest = tmp_path / "s.json"
        ok = atomic_io.write_json_atomic(dest, {"bad": {1, 2, 3}}, raise_on_error=False)
        assert ok is False
        assert not dest.exists()
        assert os.listdir(tmp_path) == []

    def test_failure_leaves_existing_dest_untouched(self, tmp_path):
        dest = tmp_path / "s.json"
        dest.write_text('{"keep": true}')
        ok = atomic_io.write_json_atomic(dest, {"bad": {1, 2}}, raise_on_error=False)
        assert ok is False
        assert json.loads(dest.read_text()) == {"keep": True}     # untouched
        assert not glob.glob(str(tmp_path / "*.tmp"))             # no stranded temp


class TestWriteTextAtomic:
    def test_writes_text_str_and_path(self, tmp_path):
        d1 = str(tmp_path / "a.txt")
        d2 = tmp_path / "b.txt"
        assert atomic_io.write_text_atomic(d1, "hello") is True
        assert atomic_io.write_text_atomic(d2, "world") is True
        assert Path(d1).read_text() == "hello"
        assert d2.read_text() == "world"

    def test_non_ascii_roundtrips(self, tmp_path):
        dest = tmp_path / "u.txt"
        atomic_io.write_text_atomic(dest, "café — 日本語")
        assert dest.read_text(encoding="utf-8") == "café — 日本語"

    def test_no_leftover_temp_on_success(self, tmp_path):
        dest = tmp_path / "a.txt"
        atomic_io.write_text_atomic(dest, "x")
        assert os.listdir(tmp_path) == ["a.txt"]


class TestFsync:
    def test_fsync_true_invokes_fsync(self, tmp_path):
        dest = tmp_path / "s.json"
        calls = []
        with patch("atomic_io.os.fsync", side_effect=lambda fd: calls.append(fd)):
            atomic_io.write_json_atomic(dest, {"a": 1}, fsync=True)
        assert calls                                    # fsync was called
        assert json.loads(dest.read_text()) == {"a": 1}

    def test_fsync_false_is_default(self, tmp_path):
        dest = tmp_path / "s.json"
        with patch("atomic_io.os.fsync",
                   side_effect=AssertionError("fsync must not be called by default")):
            atomic_io.write_json_atomic(dest, {"a": 1})  # fsync defaults False
        assert dest.exists()
