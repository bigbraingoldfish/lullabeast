"""W3-D: ensure no UI surface links to /api/metrics-global (W4-H deferred)."""

from pathlib import Path

def _ui_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "ui"


def test_index_html_has_no_metrics_global_fetch():
    html = (_ui_dir() / "index.html").read_text(encoding="utf-8", errors="replace")
    assert "metrics-global" not in html


def test_ui_client_files_have_no_metrics_global_string():
    """Static assets only — ui/server.py legitimately defines the route."""
    skip = {"server.py"}
    suffixes = {".html", ".js", ".jsx", ".ts", ".tsx", ".css", ".md"}
    for path in _ui_dir().rglob("*"):
        if not path.is_file() or path.name in skip:
            continue
        if path.suffix.lower() not in suffixes:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "metrics-global" not in text, f"unexpected reference in {path.relative_to(_ui_dir())}"
