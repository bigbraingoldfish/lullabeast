"""Tests for W5-G UI: PreflightScreen Step 3 Completion Report toggle.

Static HTML analysis. Verifies:
- New step 3 toggle renders with correct data-testid
- Old preflight check becomes step 4
- Toggle is off by default
- completionReviewOptIn is threaded into Add to Queue and Launch Now bodies
"""

import re

INDEX_HTML_PATH = "ui/index.html"


def load_html():
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def extract_function(html, func_name):
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


class TestPreflightStep3CompletionToggle:

    def test_step3_testid_still_present(self):
        """data-testid='preflight-step-3' must still exist (now the completion toggle)."""
        body = _preflight_body()
        assert 'data-testid="preflight-step-3"' in body, (
            "preflight-step-3 testid not found — W5-G step insert not implemented"
        )

    def test_step4_testid_exists(self):
        """Existing preflight check must be renumbered to data-testid='preflight-step-4'."""
        body = _preflight_body()
        assert 'data-testid="preflight-step-4"' in body, (
            "preflight-step-4 testid not found — old step-3 (preflight check) "
            "was not renumbered to step-4 in W5-G"
        )

    def test_completion_review_opt_in_state_declared(self):
        """completionReviewOptIn state variable must be declared in the component."""
        html = load_html()
        assert "completionReviewOptIn" in html, (
            "completionReviewOptIn state variable not found in index.html — "
            "W5-G toggle state not declared"
        )

    def test_step3_contains_completion_or_documentation_label(self):
        """Step 3 must contain 'completion' or 'documentation' in its copy."""
        body = _preflight_body()
        # Find step-3 region
        idx = body.find('data-testid="preflight-step-3"')
        assert idx != -1
        # Look at content between step-3 and step-4 testids
        idx4 = body.find('data-testid="preflight-step-4"', idx)
        region = body[idx: idx4] if idx4 != -1 else body[idx: idx + 600]
        region_lower = region.lower()
        assert "completion" in region_lower or "documentation" in region_lower, (
            "Step 3 content must mention 'completion' or 'documentation'"
        )

    def test_no_selection_is_default(self):
        """completionReviewOptIn must default to null (no pre-selection)."""
        html = load_html()
        assert "useState(null)" in html, (
            "completionReviewOptIn default must be null (no pre-selection) — "
            "Yes/No buttons require an explicit user choice"
        )

    def test_completion_review_opt_in_in_queue_add_body(self):
        """completionReviewOptIn must be included in at least one Add to Queue POST body.

        Note: there are multiple /api/queue/add calls in the HTML (Queue Add Modal and
        preflight onAddToQueue). We check that at least one occurrence has the flag.
        """
        html = load_html()
        assert "/api/queue/add" in html, "/api/queue/add not found in index.html"
        # Check all occurrences of /api/queue/add for the flag
        search_start = 0
        found = False
        while True:
            idx = html.find("/api/queue/add", search_start)
            if idx == -1:
                break
            context = html[max(0, idx - 300): idx + 900]
            if "completionReviewOptIn" in context or "completion_review" in context:
                found = True
                break
            search_start = idx + 1
        assert found, (
            "completionReviewOptIn / completion_review not found near any /api/queue/add call — "
            "W5-G Add to Queue submission not wired"
        )

    def test_completion_review_opt_in_in_launch_now_body(self):
        """completionReviewOptIn must be included in the Launch Now POST body."""
        html = load_html()
        assert "/api/setup/launch" in html, "/api/setup/launch not found in index.html"
        # Check all occurrences of /api/setup/launch for the flag
        search_start = 0
        found = False
        while True:
            idx = html.find("/api/setup/launch", search_start)
            if idx == -1:
                break
            context = html[max(0, idx - 300): idx + 900]
            if "completionReviewOptIn" in context or "completion_review" in context:
                found = True
                break
            search_start = idx + 1
        assert found, (
            "completionReviewOptIn / completion_review not found near any /api/setup/launch call — "
            "W5-G Launch Now submission not wired"
        )

    def test_step_3_appears_before_step_4_in_dom(self):
        """Step 3 (toggle) must appear before step 4 (preflight check) in the HTML."""
        body = _preflight_body()
        idx3 = body.find('data-testid="preflight-step-3"')
        idx4 = body.find('data-testid="preflight-step-4"')
        assert idx3 != -1, "preflight-step-3 not found"
        assert idx4 != -1, "preflight-step-4 not found"
        assert idx3 < idx4, (
            "preflight-step-3 must appear before preflight-step-4 in the DOM"
        )
