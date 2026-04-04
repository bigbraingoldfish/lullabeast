"""Playwright browser checks for unified server path inputs (hybrid with pytest).

Requires the UI server running (default http://127.0.0.1:18790). Skip if unreachable.
Override with AUTODEV_UI_E2E_URL.
"""
from __future__ import annotations

import json
import os
import re
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
    cont = page.get_by_role("button", name="Continue to AutoDev →")
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
            return navBtn !== null || txt.includes('Continue to AutoDev');
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


def test_preflight_path_has_datalist_link(pw_page):
    _goto_preflight(pw_page)
    lst = pw_page.locator("#preflight-repo-path").get_attribute("list")
    assert lst == "preflight-repo-path-recents"


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


def test_queue_modal_datalist_link(pw_page):
    page = pw_page
    page.goto(URL + "/", wait_until="domcontentloaded")
    _wait_app_shell(page)
    _dismiss_first_run_if_present(page)
    page.wait_for_selector("nav button", timeout=30000)
    page.locator("nav button").filter(has_text=re.compile(r"Project Queue", re.I)).first.click()
    page.get_by_role("button", name="+ Add Project").click()
    page.wait_for_selector("#queue-add-path", timeout=15000)
    assert page.locator("#queue-add-path").get_attribute("list") == "queue-add-path-recents"


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
    """Add empty git-less dir with roadmap.md; queue add should succeed (server git init)."""
    base = tempfile.mkdtemp(prefix="autodev-e2e-git-")
    proj = os.path.join(base, "proj")
    os.makedirs(proj, exist_ok=True)
    with open(os.path.join(proj, "roadmap.md"), "w") as f:
        f.write("- [ ] `T-E1` | LOW | Task\n  > Test.\n")

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


def test_recents_appear_after_preflight(pw_page):
    """POST preflight via API so recents list is non-empty; datalist gets options in DOM."""
    base = tempfile.mkdtemp(prefix="autodev-e2e-rc-")
    proj = os.path.join(base, "prefproj")
    os.makedirs(proj, exist_ok=True)
    with open(os.path.join(proj, "roadmap.md"), "w") as f:
        f.write("- [ ] `T-E1` | LOW | Task\n  > Test.\n")

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
        body = e.read().decode("utf-8", errors="replace")
        pytest.skip(f"Preflight HTTP {e.code} (needs full OpenClaw workspaces): {body[:500]}")
    checks = data.get("checks") or []
    if any(c.get("status") == "fail" for c in checks):
        pytest.skip(f"Preflight had failing checks (need workspaces): {checks[:3]}")

    _goto_preflight(pw_page)
    opts = pw_page.locator("#preflight-repo-path-recents option")
    assert opts.count() >= 1
