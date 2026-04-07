"""Static checks: PRD section diff UI wired in index.html (no browser)."""
import re
from pathlib import Path

_INDEX = Path(__file__).resolve().parent.parent / "ui" / "index.html"


def test_index_includes_diff_cdn_and_prd_diff_flow():
    html = _INDEX.read_text(encoding="utf-8")
    assert "unpkg.com/diff@8.0.4/dist/diff.min.js" in html
    assert "fetchPrdSectionDiff" in html
    assert "isLoadingRef" in html
    assert "/prd-section-diff" in html
    assert "/prd-section-revert" in html
    assert "Restore this section to its previous version" in html
    assert "showRevertBtn" in html
    assert 'diffStatus === "added"' in html
    assert "prdFlattenDiffLines" in html
    assert "prdCollapseDiffContext" in html


def test_revert_button_logic_excludes_added_status():
    """Added sections show New badge but must not get Revert (only modified|removed)."""
    html = _INDEX.read_text(encoding="utf-8")
    m = re.search(r"showRevertBtn\s*=\s*[^;]+;", html, re.DOTALL)
    assert m, "showRevertBtn assignment not found"
    assign = m.group()
    assert '"added"' not in assign and "'added'" not in assign
    assert "modified" in assign and "removed" in assign
