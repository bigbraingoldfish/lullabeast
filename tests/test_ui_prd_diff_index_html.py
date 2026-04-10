"""Static checks: PRD section diff UI wired in index.html (no browser)."""
import re
from pathlib import Path

_INDEX = Path(__file__).resolve().parent.parent / "ui" / "index.html"


def test_index_includes_diff_cdn_and_prd_diff_flow():
    html = _INDEX.read_text(encoding="utf-8")
    assert "/static/diff.min.js" in html
    assert "fetchPrdSectionDiff" in html
    assert "isLoadingRef" in html
    assert "/prd-section-diff" in html
    assert "/prd-section-revert" in html
    assert "Restore this section to its previous version" in html
    assert "showRevertInPanel" in html
    assert 'diffStatus === "added"' in html
    assert "prdFlattenDiffLines" in html
    assert "prdCollapseDiffContext" in html
    assert "Undo" in html
    assert "Close" in html
    assert "acceptedSectionSlugs" not in html
    assert ">Accept<" not in html


def test_revert_button_logic_excludes_added_status():
    """Added sections show New badge but must not get Revert (only modified|removed)."""
    html = _INDEX.read_text(encoding="utf-8")
    m = re.search(r"showRevertInPanel\s*=\s*[^;]+;", html, re.DOTALL)
    assert m, "showRevertInPanel assignment not found"
    assign = m.group()
    assert '"added"' not in assign and "'added'" not in assign
    assert "modified" in assign and "removed" in assign


def test_collapsed_controls_order_note_showdiff_copy():
    """Collapsed toolbar keeps controls in the strict order: Note, Show diff/Undo, Copy."""
    html = _INDEX.read_text(encoding="utf-8")
    note_idx = html.find('title="Add a note for the agent (sent with your next message)"')
    copy_idx = html.find('title="Copy section markdown"')
    show_diff_m = re.search(r">\s*Show diff\s*</button>", html)
    undo_m = re.search(r">\s*Undo\s*</button>", html)
    assert note_idx != -1 and copy_idx != -1
    assert show_diff_m and undo_m
    show_diff_idx = show_diff_m.start()
    undo_idx = undo_m.start()
    assert note_idx < show_diff_idx < copy_idx
    assert note_idx < undo_idx < copy_idx
