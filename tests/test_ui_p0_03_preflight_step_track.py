"""Static contract tests for P0-03: numbered preflight step track (ui/index.html)."""

import re

INDEX_HTML_PATH = "ui/index.html"


def load_html():
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def extract_function(html, func_name):
    """Extract the body of a named JS function from HTML."""
    match = re.search(
        rf"\n([ \t]*)function {re.escape(func_name)}\s*\([^)]*\)\s*\{{",
        html,
    )
    if not match:
        return None
    indent = match.group(1)
    body_start = match.end()
    remainder = html[body_start:]

    next_fn = re.search(rf"\n\n{re.escape(indent)}function \w", remainder)
    if next_fn:
        candidate = remainder[: next_fn.start()]
    else:
        script_end = re.search(r"\n\s*</script>", remainder)
        candidate = remainder[: script_end.start()] if script_end else remainder

    last_close = candidate.rfind(f"\n{indent}}}")
    if last_close != -1:
        return candidate[:last_close]
    return candidate


def _preflight_body():
    html = load_html()
    body = extract_function(html, "PreflightScreen")
    assert body is not None, "PreflightScreen function not found"
    return body


def test_step_1_testid_exists():
    assert 'data-testid="preflight-step-1"' in _preflight_body()


def test_step_2_testid_exists():
    assert 'data-testid="preflight-step-2"' in _preflight_body()


def test_step_3_testid_exists():
    assert 'data-testid="preflight-step-3"' in _preflight_body()


def test_step_labels_present():
    body = _preflight_body()
    assert "Step 1" in body
    assert "Step 2" in body
    assert "Step 3" in body


def test_confirm_path_renamed():
    body = _preflight_body()
    assert "Confirm path" not in body
    assert "Confirm repository" in body


def test_step1_border_green_on_locked():
    body = _preflight_body()
    i1 = body.index('data-testid="preflight-step-1"')
    i2 = body.index('data-testid="preflight-step-2"')
    slice_ = body[i1:i2]
    assert "repoPathLocked" in slice_
    assert "border-emerald" in slice_


def test_step2_border_green_on_locked():
    body = _preflight_body()
    i2 = body.index('data-testid="preflight-step-2"')
    i3 = body.index('data-testid="preflight-step-3"')
    slice_ = body[i2:i3]
    assert "roadmapSeedLocked" in slice_
    assert "border-emerald" in slice_


def test_step3_border_states():
    body = _preflight_body()
    i3 = body.index('data-testid="preflight-step-3"')
    i4 = body.index('data-testid="preflight-launch-blocking-hint"', i3)
    step3_block = body[i3:i4]
    assert "border-red" in step3_block
    assert "border-amber" in step3_block
    assert "border-emerald" in step3_block
    assert "border-[#2a2d31]" in step3_block


def test_launch_blocking_hint_testid():
    assert 'data-testid="preflight-launch-blocking-hint"' in _preflight_body()


def test_blocking_hint_messages():
    body = _preflight_body()
    assert "Complete Step 1" in body
    assert "Complete Step 2" in body
    assert "Complete Step 3" in body


def test_per_step_descriptions():
    body = _preflight_body()
    assert "Confirm the repository path AutoDev will build in." in body
    assert "Format check only - content quality review lives in Project Ideas if needed." in body
    assert "Checks symlink, .gitignore, workspace files, and roadmap." in body
