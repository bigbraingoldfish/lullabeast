"""Regression: Setup screen fetch handlers must not treat HTTP errors as {valid: false}."""

from pathlib import Path

INDEX = Path(__file__).parent.parent / "ui" / "index.html"


def test_repo_path_confirm_checks_response_ok_before_valid_field():
    """FastAPI errors use `detail`, not `error`; without `r.ok` check the UI showed 'Invalid path'."""
    text = INDEX.read_text()
    assert "onRepoPathConfirm" in text
    assert "if (!r.ok)" in text
    assert "validate-repo-path" in text
    assert "d.detail" in text or "detail" in text
