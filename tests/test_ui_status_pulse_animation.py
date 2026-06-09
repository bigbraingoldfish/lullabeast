"""Tests for status pulse animation on different pipeline states."""
import pytest
import re
from pathlib import Path

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html_content():
    if not INDEX_HTML_PATH.exists():
        pytest.fail(f"index.html not found at {INDEX_HTML_PATH}")
    return INDEX_HTML_PATH.read_text()


def test_running_state_is_calm_green_tinted_pill_no_dot(html_content):
    """RUNNING is the calm green tinted pill (#4fd98c text + tinted bg + soft outline) with
    NO leading status dot — the .pill-glow / .pill-dot ::before dot was removed, and the
    legacy full-fill run-pulse animation is not used either."""
    m = re.search(r"RUNNING:\s*\{[^}]*\}", html_content)
    assert m, "RUNNING entry in PIPELINE_LIVE_PILL expected"
    frag = m.group(0)
    assert "#4fd98c" in frag, "RUNNING should use the calm brand green #4fd98c"
    assert "border" in frag, "RUNNING should keep the soft outline"
    assert "pill-glow" not in frag and "pill-dot" not in frag, \
        "RUNNING pill must not carry a status dot (pill-glow/pill-dot removed)"
    assert "run-pulse" not in frag, "RUNNING must not use the legacy run-pulse full-fill"


def test_waiting_for_sentinel_state_has_no_pulse_class(html_content):
    """WAITING_FOR_SENTINEL is static teal — no run-pulse or status-pulse in mapping."""
    bad = bool(re.search(r"WAITING_FOR_SENTINEL:\s*\{[^}]*run-pulse", html_content))
    assert not bad, "WAITING_FOR_SENTINEL must not use run-pulse"
    bad2 = bool(re.search(r"WAITING_FOR_SENTINEL:\s*\{[^}]*status-pulse", html_content))
    assert not bad2, "WAITING_FOR_SENTINEL must not use status-pulse in pipeline pill map"


def test_waiting_for_human_state_has_no_pulse_animation(html_content):
    """WAITING_FOR_HUMAN is a calm orange-tinted pill with an attention glow-dot
    (.pill-glow), not the legacy run-pulse full-fill / solid bg-orange-500."""
    assert not re.search(r"WAITING_FOR_HUMAN:\s*\{[^}]*run-pulse", html_content), \
        "WAITING_FOR_HUMAN must not use the legacy run-pulse full-fill"
    assert re.search(r"WAITING_FOR_HUMAN:\s*\{[^}]*text-orange-300", html_content), \
        "WAITING_FOR_HUMAN should use the calm orange-tint pill (text-orange-300)"


def test_halted_silent_state_has_no_pulse_animation(html_content):
    """HALTED_SILENT has NO pulse (static red)."""
    has_no_pulse = not bool(re.search(r"HALTED_SILENT:\s*\{[^}]*run-pulse", html_content))
    assert has_no_pulse, "HALTED_SILENT state should NOT have run-pulse"


def test_blocked_state_has_no_pulse_animation(html_content):
    """Pipeline BLOCKED has NO pulse (static red) in PIPELINE_LIVE_PILL map."""
    idx = html_content.find("PIPELINE_LIVE_PILL = {")
    assert idx != -1, "PIPELINE_LIVE_PILL map expected"
    end = html_content.find("};", idx)
    chunk = html_content[idx:end] if end != -1 else html_content[idx : idx + 4000]
    block = re.search(r"BLOCKED\s*:\s*\{[^}]+\}", chunk)
    assert block, "BLOCKED entry in PIPELINE_LIVE_PILL"
    assert "run-pulse" not in block.group(0), "Pipeline BLOCKED must not use run-pulse"


def test_run_pulse_css_class_exists(html_content):
    """run-pulse CSS class is defined with teal-tinted animation."""
    has_run_pulse_css = bool(re.search(r"\.run-pulse\s*\{", html_content))
    assert has_run_pulse_css, "run-pulse CSS class not defined"
    has_teal_keyframes = bool(re.search(r"@keyframes\s+teal-pulse|teal-pulse", html_content))
    assert has_teal_keyframes, "teal-pulse keyframes or animation reference not found"


def test_legacy_status_pulse_may_exist_for_non_pipeline_ui(html_content):
    """amber-pulse keyframes may remain for legacy UI (e.g. stopping indicator)."""
    has_keyframes = bool(re.search(r"@keyframes\s+amber-pulse", html_content))
    assert has_keyframes, "amber-pulse keyframes may still exist for non-pipeline pills"
