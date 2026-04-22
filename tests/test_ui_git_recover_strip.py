"""Static tests for Pipeline Monitor git recovery strip + modal (L-32 bundle)."""
import re
from pathlib import Path

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"


def test_recover_git_button_has_title_stash_checkout_not_reset():
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"Recover Git\s*</button>",
        html,
    )
    assert m, "Recover Git button not found"
    start = max(0, m.start() - 2600)
    chunk = html[start : m.end()]
    assert "title=" in chunk, "Recover Git button should have native title tooltip"
    assert "stash" in chunk.lower() or "Stash" in chunk, "title should mention stash"
    assert "checkout" in chunk.lower() or "Check out" in chunk, "title should mention checkout"
    # Title may say we do not run git reset (clarification), but must not instruct user to run reset.
    assert "git reset --" not in chunk.lower()


def test_git_recover_modal_branch_to_return_to_and_prefill_copy():
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert "Branch to return to" in html
    assert "git_recover_suggested_branch" in html
    assert "Return repository to branch" in html
    assert re.search(r"prefill|Prefill|settings|active project", html, re.I), (
        "modal body should mention prefilled context"
    )


def test_recover_git_opens_modal_with_suggested_branch_not_hardcoded_main_only():
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert "git_recover_suggested_branch" in html
    assert "setGitRecoverBranch" in html
    assert re.search(r"pState\.git_recover_suggested_branch", html), (
        "Recover Git open should read suggested branch from pipeline state"
    )


def test_pipeline_state_initial_includes_git_recover_suggested_branch_key():
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert "git_recover_suggested_branch" in html
