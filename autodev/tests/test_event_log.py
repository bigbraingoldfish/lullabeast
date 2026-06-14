"""P1-A — the shared event-log appender + size-based rotation (event_log.py).

``autodev/pipeline/event_log.py`` is the single source of truth for the
``pipeline_events.jsonl`` file contract. Both writers — the orchestrator
(``_write_pipeline_event``) and the UI server (``_write_operator_event``) — call
``append_pipeline_event`` so the schema, the JSON-line format, and the size-based
rotation live in exactly one place.

These pin:
- a single append writes exactly one JSON line;
- a missing parent directory is created (telemetry must not require pre-seeding);
- the file rotates to a timestamped archive once it exceeds ``max_bytes`` and the
  fresh live file keeps receiving writes;
- rotation prunes archives down to ``keep`` (bounded disk);
- the env-config coercion falls back to the default on garbage.

A regression that drops rotation (unbounded growth — the integrity finding this
closes) or corrupts the append is exactly what these catch.
"""
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import event_log  # noqa: E402


def test_append_writes_one_json_line(tmp_path):
    """One call → one parseable JSON line carrying the entry verbatim."""
    p = tmp_path / "pipeline_events.jsonl"
    event_log.append_pipeline_event(str(p), {"event": "gate_pass", "ts": "T", "run_id": "R1"})
    lines = [l for l in p.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["event"] == "gate_pass"
    assert row["run_id"] == "R1"


def test_append_creates_parent_dir(tmp_path):
    """A missing parent directory is created rather than raising — the appender
    must never require the caller to pre-create the events dir."""
    p = tmp_path / "nested" / "deeper" / "pipeline_events.jsonl"
    event_log.append_pipeline_event(str(p), {"event": "x"})
    assert p.exists()


def test_append_appends_not_truncates(tmp_path):
    """Successive appends accumulate (O_APPEND), they do not overwrite."""
    p = tmp_path / "pipeline_events.jsonl"
    for i in range(3):
        event_log.append_pipeline_event(str(p), {"event": "e", "i": i})
    lines = [l for l in p.read_text().splitlines() if l.strip()]
    assert [json.loads(l)["i"] for l in lines] == [0, 1, 2]


def test_rotation_archives_and_starts_fresh(tmp_path):
    """Once the live file exceeds ``max_bytes`` it is renamed to a timestamped
    archive (``pipeline_events.<ts>.jsonl``) and a fresh live file keeps growing.
    Without rotation the file grows unbounded (the integrity finding this closes)."""
    p = tmp_path / "pipeline_events.jsonl"
    for i in range(60):
        event_log.append_pipeline_event(
            str(p), {"event": "e", "i": i, "pad": "x" * 80}, max_bytes=300, keep=10
        )
    archives = list(tmp_path.glob("pipeline_events.*.jsonl"))
    assert archives, "expected at least one rotated archive"
    # The live file name is excluded from the archive glob and still present.
    assert p.exists()
    assert p.name not in {a.name for a in archives}


def test_rotation_prunes_to_keep(tmp_path):
    """Rotation keeps only the most recent ``keep`` archives, bounding disk use."""
    p = tmp_path / "pipeline_events.jsonl"
    for i in range(300):
        event_log.append_pipeline_event(
            str(p), {"event": "e", "i": i, "pad": "y" * 80}, max_bytes=200, keep=3
        )
    archives = list(tmp_path.glob("pipeline_events.*.jsonl"))
    assert len(archives) <= 3, f"prune should cap archives at keep=3, got {len(archives)}"


def test_no_rotation_under_threshold(tmp_path):
    """A small file is never rotated — no spurious archives."""
    p = tmp_path / "pipeline_events.jsonl"
    for i in range(5):
        event_log.append_pipeline_event(str(p), {"event": "e", "i": i}, max_bytes=10_000_000, keep=5)
    assert not list(tmp_path.glob("pipeline_events.*.jsonl"))


def test_env_config_coercion_falls_back_on_garbage(monkeypatch):
    """A non-numeric env override falls back to the default rather than crashing
    the appender (mirrors load_config's numeric coercion discipline)."""
    monkeypatch.setenv("AUTODEV_EVENTS_MAX_BYTES", "25 megabytes")
    assert event_log._resolve_positive_int("AUTODEV_EVENTS_MAX_BYTES", 123) == 123
    monkeypatch.setenv("AUTODEV_EVENTS_MAX_BYTES", "5000000")
    assert event_log._resolve_positive_int("AUTODEV_EVENTS_MAX_BYTES", 123) == 5000000
    monkeypatch.setenv("AUTODEV_EVENTS_MAX_BYTES", "-4")
    assert event_log._resolve_positive_int("AUTODEV_EVENTS_MAX_BYTES", 123) == 123
