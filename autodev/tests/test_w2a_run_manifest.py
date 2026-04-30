"""
W2-A: run_manifest.json written at run start.

Tests verify:
- _write_run_manifest helper exists and is called in the right place
- run_manifest.json is written atomically to PROJECT_ARTIFACTS_DIR
- Roadmap is parsed for phase_count, subsystem_set, total_goals_chars
- Graceful on missing roadmap (writes file with phase_count=0, no crash)
- No leftover temp files after write

Pattern: source-text presence tests (fast, no I/O) + runtime tests via tmp_path.
"""
import json
import pathlib

import autodev.pipeline.orchestrator as _orch_mod

_ORCH = pathlib.Path(__file__).parent.parent / "pipeline" / "orchestrator.py"
_SRC = _ORCH.read_text()


# ---------------------------------------------------------------------------
# Source-text presence tests (fast, no filesystem)
# ---------------------------------------------------------------------------

def test_write_run_manifest_helper_defined():
    assert "def _write_run_manifest(" in _SRC, (
        "_write_run_manifest helper not found in orchestrator.py — "
        "add a module-level helper that writes run_manifest.json at run start."
    )


def test_run_manifest_written_after_queue_write():
    """_write_run_manifest must be called after _write_queue inside _select_next_queue_project."""
    lines = _SRC.splitlines()
    write_queue_lines = [i for i, l in enumerate(lines, 1) if "_write_queue(queue_data)" in l]
    assert write_queue_lines, "_write_queue(queue_data) call not found in orchestrator"
    write_manifest_lines = [i for i, l in enumerate(lines, 1) if "_write_run_manifest(" in l]
    assert write_manifest_lines, "_write_run_manifest call not found in orchestrator"
    assert any(m > q for m in write_manifest_lines for q in write_queue_lines), (
        "_write_run_manifest must be called after _write_queue(queue_data) — "
        "it should write the manifest after the queue entry is committed."
    )


def test_run_manifest_written_before_state_dict_construction():
    """run_manifest should be written before self.state = {...} in _select_next_queue_project."""
    lines = _SRC.splitlines()
    manifest_lines = [i for i, l in enumerate(lines, 1) if "_write_run_manifest(" in l]
    state_lines = [i for i, l in enumerate(lines, 1) if "self.state = {" in l]
    assert manifest_lines and state_lines
    assert any(m < s for m in manifest_lines for s in state_lines if s > m), (
        "_write_run_manifest must be called before self.state = {...} "
        "in _select_next_queue_project — manifest is captured before state is reset."
    )


def test_run_manifest_path_uses_project_artifacts_dir():
    assert '"run_manifest.json"' in _SRC or "'run_manifest.json'" in _SRC, (
        "No 'run_manifest.json' string literal found in orchestrator. "
        "The helper must construct the path under PROJECT_ARTIFACTS_DIR."
    )


def test_run_manifest_includes_required_schema_fields():
    for field in ("phase_count", "subsystem_set", "project_path", "project_name",
                  "queue_entry_id", "started_at", "schema_version"):
        assert field in _SRC, (
            f"run_manifest schema field '{field}' not referenced in orchestrator. "
            "Add it to the manifest dict written by _write_run_manifest."
        )


# ---------------------------------------------------------------------------
# Runtime tests
# ---------------------------------------------------------------------------

def _patch_pad(tmp_art_dir, fn):
    """Run fn with PROJECT_ARTIFACTS_DIR patched to tmp_art_dir."""
    orig = _orch_mod.PROJECT_ARTIFACTS_DIR
    _orch_mod.PROJECT_ARTIFACTS_DIR = str(tmp_art_dir)
    try:
        fn()
    finally:
        _orch_mod.PROJECT_ARTIFACTS_DIR = orig


def _make_entry(tmp_path, *, idea_id="idea-1"):
    return {
        "id": "test-id",
        "name": "MyProject",
        "project_path": str(tmp_path),
        "idea_id": idea_id,
        "started_at": "2026-04-30T12:00:00Z",
    }


def test_write_run_manifest_creates_file_with_correct_fields(tmp_path):
    """_write_run_manifest writes run_manifest.json with all required schema fields."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    # Minimal roadmap with two phases
    (tmp_path / "roadmap.md").write_text(
        "| `CORE-E1` | x | Build core |\n"
        "| `UI-E1`   | x | Build UI   |\n"
    )
    entry = _make_entry(tmp_path)

    _patch_pad(art, lambda: _orch_mod._write_run_manifest(entry))

    manifest_path = art / "run_manifest.json"
    assert manifest_path.exists(), "run_manifest.json not written to PROJECT_ARTIFACTS_DIR"
    data = json.loads(manifest_path.read_text())

    assert data["schema_version"] == 1
    assert data["project_name"] == "MyProject"
    assert data["queue_entry_id"] == "test-id"
    assert data["idea_id"] == "idea-1"
    assert data["started_at"] == "2026-04-30T12:00:00Z"
    assert isinstance(data["phase_count"], int) and data["phase_count"] == 2
    assert isinstance(data["subsystem_set"], list)
    assert sorted(data["subsystem_set"]) == ["CORE", "UI"]
    assert isinstance(data["total_goals_chars"], int)


def test_write_run_manifest_graceful_on_missing_roadmap(tmp_path):
    """Missing roadmap.md must not abort — file still written with phase_count=0."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    entry = _make_entry(tmp_path)

    _patch_pad(art, lambda: _orch_mod._write_run_manifest(entry))

    manifest_path = art / "run_manifest.json"
    assert manifest_path.exists(), "run_manifest.json not written even when roadmap is absent"
    data = json.loads(manifest_path.read_text())
    assert data["phase_count"] == 0
    assert data["subsystem_set"] == []


def test_write_run_manifest_graceful_on_corrupt_roadmap(tmp_path):
    """Corrupt roadmap content must not crash — file written with what was parseable."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    (tmp_path / "roadmap.md").write_text("\x00\x00\x00 binary garbage \x00")
    entry = _make_entry(tmp_path)

    _patch_pad(art, lambda: _orch_mod._write_run_manifest(entry))

    assert (art / "run_manifest.json").exists()


def test_write_run_manifest_uses_atomic_write(tmp_path):
    """run_manifest.json must be written atomically — no temp file left behind."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    entry = _make_entry(tmp_path)

    _patch_pad(art, lambda: _orch_mod._write_run_manifest(entry))

    files = [f.name for f in art.iterdir()]
    assert files == ["run_manifest.json"], (
        f"Expected only run_manifest.json in artifacts dir after write, got: {files}. "
        "Use mkstemp + os.replace to avoid leaving temp files."
    )


def test_write_run_manifest_none_idea_id(tmp_path):
    """idea_id=None is a valid entry; must write null (not crash)."""
    art = tmp_path / ".autodev" / "pipeline"
    art.mkdir(parents=True)
    entry = _make_entry(tmp_path, idea_id=None)

    _patch_pad(art, lambda: _orch_mod._write_run_manifest(entry))

    data = json.loads((art / "run_manifest.json").read_text())
    assert data["idea_id"] is None


def test_write_run_manifest_creates_artifacts_dir_if_absent(tmp_path):
    """Helper must create PROJECT_ARTIFACTS_DIR if it doesn't exist yet."""
    art = tmp_path / ".autodev" / "pipeline"
    # Intentionally NOT creating art dir
    entry = _make_entry(tmp_path)

    _patch_pad(art, lambda: _orch_mod._write_run_manifest(entry))

    assert (art / "run_manifest.json").exists(), (
        "run_manifest.json not written — helper must call os.makedirs(PROJECT_ARTIFACTS_DIR, exist_ok=True)"
    )
