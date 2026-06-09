"""Playwright browser checks for unified server path inputs (hybrid with pytest).

Requires the UI server running (default http://127.0.0.1:18790). Skip if unreachable.
Override with AUTODEV_UI_E2E_URL.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request

import pytest

URL = os.environ.get("AUTODEV_UI_E2E_URL", "http://127.0.0.1:18790")


def _server_ok() -> bool:
    try:
        import urllib.request

        r = urllib.request.urlopen(URL + "/", timeout=3)
        return r.status == 200
    except Exception:
        return False


@pytest.fixture
def pw_page():
    if not _server_ok():
        pytest.skip(f"AutoDev UI not reachable at {URL} (start uvicorn on port 18790)")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()


def _dismiss_first_run_if_present(page):
    # Brand-agnostic: the first-run continue button is "Continue to <Product> →"
    # (the product name is themed — was "AutoDev", now "Lullabeast"). Match on the
    # stable prefix so a future rebrand of index.html can't silently re-break this.
    cont = page.get_by_role("button", name=re.compile(r"Continue to .+ →"))
    if cont.count() > 0:
        try:
            cont.first.click(timeout=5000)
            time.sleep(0.5)
        except Exception:
            pass


def _wait_app_shell(page):
    """Wait until sidebar exists or first-run continue is shown (not stuck on Loading)."""
    page.wait_for_function(
        """() => {
            const navBtn = document.querySelector('nav button');
            const txt = document.body ? document.body.innerText : '';
            // Brand-agnostic first-run check (see _dismiss_first_run_if_present).
            return navBtn !== null || txt.includes('Continue to ');
        }""",
        timeout=90000,
    )


def _goto_preflight(page):
    page.goto(URL + "/", wait_until="domcontentloaded", timeout=60000)
    _wait_app_shell(page)
    _dismiss_first_run_if_present(page)
    page.wait_for_selector("nav button", timeout=30000)
    # Text node is "Setup & Preflight" (accessible name); role+name matching is flaky in some themes.
    page.locator("nav button").filter(has_text=re.compile(r"Preflight")).first.click(timeout=20000)
    page.wait_for_selector("#preflight-repo-path", timeout=20000)


def test_preflight_debounce_shows_check(pw_page):
    tmp = tempfile.mkdtemp(prefix="autodev-e2e-")
    _goto_preflight(pw_page)
    pw_page.locator("#preflight-repo-path").fill(tmp)
    time.sleep(0.7)
    pw_page.locator('span[title="Directory exists on the server"]').wait_for(
        state="visible", timeout=10000
    )


def test_preflight_bad_path_shows_x(pw_page):
    _goto_preflight(pw_page)
    pw_page.locator("#preflight-repo-path").fill("/nonexistent/autodev-e2e-bad-path-xyz")
    time.sleep(0.7)
    pw_page.locator('span[title="Path does not exist or is not reachable"]').wait_for(
        state="visible", timeout=10000
    )


def test_preflight_path_no_datalist_autofill_off(pw_page):
    """4-A/N2 — the recents <datalist> is gone (replaced by a visible click-to-fill list) and
    browser autofill is disabled so a previously-typed path can't re-populate the field."""
    _goto_preflight(pw_page)
    inp = pw_page.locator("#preflight-repo-path")
    assert inp.get_attribute("list") is None, "datalist link must be removed (4-A)"
    assert inp.get_attribute("autocomplete") == "off", "browser autofill must be off (N2)"


def test_queue_modal_debounce_shows_check(pw_page):
    tmp = tempfile.mkdtemp(prefix="autodev-e2e-q-")
    page = pw_page
    page.goto(URL + "/", wait_until="domcontentloaded")
    _wait_app_shell(page)
    _dismiss_first_run_if_present(page)
    page.wait_for_selector("nav button", timeout=30000)
    page.locator("nav button").filter(has_text=re.compile(r"Project Queue", re.I)).first.click()
    page.get_by_role("button", name="+ Add Project").click()
    page.wait_for_selector("#queue-add-path", timeout=15000)
    page.locator("#queue-add-path").fill(tmp)
    time.sleep(0.7)
    page.locator('span[title="Directory exists on the server"]').wait_for(
        state="visible", timeout=10000
    )


def test_queue_modal_no_datalist_autofill_off(pw_page):
    """4-A/N2 — the Add-Project path input has no recents datalist and disables browser autofill."""
    page = pw_page
    page.goto(URL + "/", wait_until="domcontentloaded")
    _wait_app_shell(page)
    _dismiss_first_run_if_present(page)
    page.wait_for_selector("nav button", timeout=30000)
    page.locator("nav button").filter(has_text=re.compile(r"Project Queue", re.I)).first.click()
    page.get_by_role("button", name="+ Add Project").click()
    page.wait_for_selector("#queue-add-path", timeout=15000)
    inp = page.locator("#queue-add-path")
    assert inp.get_attribute("list") is None, "datalist link must be removed (4-A)"
    assert inp.get_attribute("autocomplete") == "off", "browser autofill must be off (N2)"


def test_switch_modal_debounce_shows_check(pw_page):
    tmp = tempfile.mkdtemp(prefix="autodev-e2e-sw-")
    page = pw_page
    page.goto(URL + "/", wait_until="domcontentloaded")
    _wait_app_shell(page)
    _dismiss_first_run_if_present(page)
    change = page.get_by_role("button", name="Change")
    if change.count() == 0 or not change.first.is_enabled():
        pytest.skip("Pipeline Change control not available")
    change.first.click()
    try:
        page.wait_for_selector("#switch-path", state="visible", timeout=5000)
    except Exception:
        pytest.skip(
            "Switch project modal did not open (Change opens stop dialog when pipeline is running)"
        )
    page.locator("#switch-path").fill(tmp)
    time.sleep(0.7)
    page.locator('span[title="Directory exists on the server"]').wait_for(
        state="visible", timeout=10000
    )


def test_switch_modal_no_select_element(pw_page):
    page = pw_page
    page.goto(URL + "/", wait_until="domcontentloaded")
    _wait_app_shell(page)
    _dismiss_first_run_if_present(page)
    change = page.get_by_role("button", name="Change")
    if change.count() == 0 or not change.first.is_enabled():
        pytest.skip("Pipeline Change control not available")
    change.first.click()
    try:
        page.wait_for_selector("#switch-path", state="visible", timeout=5000)
    except Exception:
        pytest.skip(
            "Switch project modal did not open (Change opens stop dialog when pipeline is running)"
        )
    dialog = page.locator('[role="dialog"]').filter(has=page.locator("#switch-path"))
    assert dialog.locator("select").count() == 0


def test_queue_add_autorepairs_git(pw_page):
    """Add empty git-less dir with roadmap.md + verification.md; queue add
    should succeed (server git init).

    The fixture writes the minimal P0-compliant project shape (roadmap with
    Behavioral Verification block, verification.md with all five required
    headings). Both files are required by Stage C's strict preflight; the
    test's actual concern is the *git auto-init* step that happens after
    preflight passes. Shape mirrors ``tests/test_queue_api.py`` line ~361,
    the canonical server-side fixture for this contract.
    """
    base = tempfile.mkdtemp(prefix="autodev-e2e-git-")
    proj = os.path.join(base, "proj")
    os.makedirs(proj, exist_ok=True)
    with open(os.path.join(proj, "roadmap.md"), "w") as f:
        f.write(
            "- [ ] `T-E1` | LOW | Task\n"
            "  > Test.\n"
            "  **Behavioral Verification:**\n"
            "  - **User-observable:** It works.\n"
            "  - **How we'll check:** Run it.\n"
            "  - **If this fails, the user sees:** Nothing.\n"
        )
    with open(os.path.join(proj, "verification.md"), "w") as f:
        f.write(
            "# Verification\n\n"
            "## Project type\ncli\n\n"
            "## Entry point\n- Command: `x`\n- Ready signal: ok\n\n"
            "## Public surface\n1. do thing\n\n"
            "## Verification stack\n- Acceptance tool: subprocess + assertions\n"
        )

    page = pw_page
    page.goto(URL + "/", wait_until="domcontentloaded")
    _wait_app_shell(page)
    _dismiss_first_run_if_present(page)
    page.wait_for_selector("nav button", timeout=30000)
    page.locator("nav button").filter(has_text=re.compile(r"Project Queue", re.I)).first.click()
    page.get_by_role("button", name="+ Add Project").click()
    page.wait_for_selector("#queue-add-path", timeout=15000)
    page.locator("#queue-add-path").fill(proj)
    time.sleep(0.5)
    page.get_by_role("button", name="Validate & Add").click()
    # Wait for modal to close (success) or error box
    time.sleep(2)
    if page.get_by_text("Add Project to Queue").count() > 0:
        err = page.locator(".text-red-300").all_text_contents()
        pytest.fail(f"Queue add failed (modal still open): {err}")

    assert os.path.isdir(os.path.join(proj, ".git")), "Expected git init on server for queue add"

    # Clean up: remove the test entry from the queue so it doesn't accumulate across runs.
    try:
        with urllib.request.urlopen(URL + "/api/queue", timeout=10) as resp:
            entries = json.loads(resp.read().decode())
        for entry in entries.get("queue", []):
            if entry.get("project_path") == proj:
                req = urllib.request.Request(
                    URL + f"/api/queue/{entry['id']}",
                    method="DELETE",
                )
                urllib.request.urlopen(req, timeout=10).close()
                break
    except Exception:
        pass  # Best-effort cleanup — don't fail the test if removal fails


def test_recents_appear_after_preflight(pw_page):
    """POST preflight via API so recents list is non-empty; the click-to-fill list shows it.

    Fixture writes the canonical P0-compliant project shape (roadmap with a
    Behavioral Verification block, ``verification.md`` with all five required
    sections). Both files are required by Stage C's strict preflight; the
    test's actual concern is that a successful preflight call populates the
    recents list and the UI exposes it as a click-to-fill button. The project lives under
    $HOME (not /tmp) so it survives the ``recentsToDisplayPaths`` /tmp display backstop.

    Skip-handling discipline (P1 Stage B): the only sanctioned skip path is
    a missing OpenClaw workspace install — every other failure mode raises
    ``pytest.fail`` rather than silently masking a real regression.

    Cleanup: the test removes its tmpdir on disk and POSTs
    ``/api/setup/recent-projects/prune`` so the recents JSON self-cleans
    rather than accumulating one stale ``/tmp/autodev-e2e-rc-*`` entry per
    run. Prune sweeps the entry because its directory no longer exists.
    """
    base = tempfile.mkdtemp(prefix=".autodev-e2e-rc-", dir=os.path.expanduser("~"))
    try:
        proj = os.path.join(base, "prefproj")
        os.makedirs(proj, exist_ok=True)
        # ``> Test:`` (colon) is the form _validate_roadmap_content's regex
        # accepts. A period would be rejected if seed-validation ever runs
        # on existing on-disk roadmaps — defensive consistency with the
        # canonical fixture shape in test_queue_add_autorepairs_git above.
        with open(os.path.join(proj, "roadmap.md"), "w") as f:
            f.write(
                "- [ ] `T-E1` | LOW | Task\n"
                "  > Test: It works.\n"
                "  **Behavioral Verification:**\n"
                "  - **User-observable:** It works.\n"
                "  - **How we'll check:** Run it.\n"
                "  - **If this fails, the user sees:** Nothing.\n"
            )
        # verification.md required by _run_preflight_checks step 7 (strict
        # mode, no opt-out). Five sections, each with a non-empty body.
        with open(os.path.join(proj, "verification.md"), "w") as f:
            f.write(
                "# Verification\n\n"
                "## Project type\ncli\n\n"
                "## Entry point\n- Command: `x`\n- Ready signal: ok\n\n"
                "## Public surface\n1. do thing\n\n"
                "## Verification stack\n- Acceptance tool: subprocess + assertions\n"
            )

        body = json.dumps({"repo_path": proj}).encode("utf-8")
        req = urllib.request.Request(
            URL + "/api/setup/preflight",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            # An HTTP error is a real server-side bug, not a CI artefact —
            # fail loud instead of silently skipping.
            pytest.fail(f"Preflight HTTP {e.code}: {err_body[:500]}")

        checks = data.get("checks") or []
        fails = [c for c in checks if c.get("status") == "fail"]
        # Only workspace-{agent} failures are a sanctioned skip case (CI
        # without a full OpenClaw install). Any other failure means the
        # fixture or the endpoint regressed and should fail loud.
        non_workspace_fails = [
            c for c in fails if not str(c.get("check", "")).startswith("workspace-")
        ]
        if non_workspace_fails:
            pytest.fail(
                f"Preflight failed with non-workspace issues "
                f"(fixture should satisfy these): {non_workspace_fails}"
            )
        if fails:
            pytest.skip(
                f"Preflight workspace checks failed (CI without OpenClaw): {fails}"
            )

        _goto_preflight(pw_page)
        recents = pw_page.locator('[data-testid="server-path-recents"]')
        recents.first.wait_for(state="visible", timeout=10000)
        assert recents.locator("button", has_text=proj).count() >= 1, (
            "the preflighted project must appear as a click-to-fill recent"
        )
    finally:
        # Remove tmpdir so prune sweeps the recents entry. Best-effort: a
        # cleanup failure must not mask a real test failure.
        shutil.rmtree(base, ignore_errors=True)
        try:
            prune_req = urllib.request.Request(
                URL + "/api/setup/recent-projects/prune",
                data=b"",
                method="POST",
            )
            urllib.request.urlopen(prune_req, timeout=10).close()
        except Exception:
            pass
