"""Static lint: the README names a local model as validated only after a run.

The "Fully local" row of README.md's "Run it your way" table records which
local models a full pipeline run has been validated on. Qwen3.6 27B (MTP and
plain, q8_0) has been; Qwen3.8 27B is listed beside it with its validation
run pending. This guards both halves: the validated entries stay, and the
newer model is not promoted to "validated" without its run. When Qwen3.8's
validation run lands, move it into the "Validated on" clause and replace the
pending check with a positive one.
"""
from pathlib import Path

_README = Path(__file__).resolve().parents[1] / "README.md"


def _fully_local_row() -> str:
    rows = [
        line
        for line in _README.read_text(encoding="utf-8").splitlines()
        if line.startswith("| **Fully local** |")
    ]
    assert len(rows) == 1, "README must have exactly one 'Fully local' row"
    return rows[0]


def test_validated_qwen36_entries_stay():
    row = _fully_local_row()
    assert "Validated on" in row
    assert "`unsloth/Qwen3.6-27B-MTP-GGUF`" in row
    assert "`llamacpp/Qwen3.6-27B`" in row


def test_qwen38_is_listed_as_pending_not_validated():
    row = _fully_local_row()
    assert "Qwen3.8 27B" in row
    clause = row[row.index("Qwen3.8 27B"):].split(";", 1)[0]
    assert "pending" in clause, f"Qwen3.8 must read as pending: {clause!r}"
    start = row.index("Validated on")
    validated = row[start:row.index("(both q8_0)", start)]
    assert "Qwen3.8" not in validated
