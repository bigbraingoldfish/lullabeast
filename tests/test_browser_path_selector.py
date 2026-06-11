"""Playwright browser checks for unified server path inputs (hybrid with pytest).

Requires the UI server running (default http://127.0.0.1:18790). Skip if unreachable.
Override with AUTODEV_UI_E2E_URL. Against a token-protected dashboard (AUTODEV_UI_TOKEN
auth), set AUTODEV_UI_E2E_TOKEN to the dashboard token — a deliberate explicit opt-in,
separate from AUTODEV_UI_TOKEN (which the conftest scrubs so a sourced .env cannot point
these state-mutating tests at a live dashboard by accident).

Requires the optional Python ``playwright`` dev dependency (pinned in requirements-dev.txt).
The module skips loudly at collection when it is absent — see the ``pytest.importorskip`` gate
below and CHANGELOG "P1 Stage B".
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

# This module drives a real browser through Playwright's *Python* sync API — an optional
# dev/test dependency (pinned in requirements-dev.txt; install.sh installs only the Node
# visual-review MCP + Chromium, not the Python bindings). Gate the whole module on it so a
# missing package skips loudly at collection (one clear skip) instead of raising fixture-setup
# ImportErrors once the UI server is reachable. Loud + narrow: see CHANGELOG "P1 Stage B".
sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright not installed — pip install playwright && playwright install chromium",
)

URL = os.environ.get("AUTODEV_UI_E2E_URL", "http://127.0.0.1:18790")

# Dashboard access token for token-protected installs (_TokenAuthMiddleware). Read at import
# time, like URL. Deliberately NOT a fallback to AUTODEV_UI_TOKEN: the conftest scrubs that
# var per-test precisely so a sourced .env cannot poison the suite, and pointing these
# state-mutating tests (queue add/delete, preflight → recents) at a live tokenized dashboard
# must be an explicit operator opt-in.
_E2E_TOKEN = os.environ.get("AUTODEV_UI_E2E_TOKEN", "").strip()


def _auth_headers() -> dict:
    """``Authorization: Bearer`` header for every server call, or ``{}`` when auth is off."""
    return {"Authorization": f"Bearer {_E2E_TOKEN}"} if _E2E_TOKEN else {}


def _context_kwargs() -> dict:
    """Per-test browser-context kwargs — bearer header on every page request, or bare."""
    return {"extra_http_headers": _auth_headers()} if _E2E_TOKEN else {}


def _probe_server() -> str:
    """Reachability probe: ``"ok"`` / ``"auth_rejected"`` / ``"unreachable"``.

    The tri-state keeps the skip loud and narrow (CHANGELOG "P1 Stage B"): a 401/403 means the
    server is alive but the dashboard token is required (or wrong) — steering the operator to
    AUTODEV_UI_E2E_TOKEN — while any other failure is the plain server-down case. A non-auth
    HTTP error (e.g. 500) deliberately reads as unreachable, not auth_rejected.
    """
    try:
        req = urllib.request.Request(URL + "/", headers=_auth_headers())
        with urllib.request.urlopen(req, timeout=3) as r:
            return "ok" if r.status == 200 else "unreachable"
    except urllib.error.HTTPError as e:
        return "auth_rejected" if e.code in (401, 403) else "unreachable"
    except Exception:
        return "unreachable"


def _is_missing_browser_error(err) -> bool:
    """True only for Playwright's "browser binary not installed" launch error.

    Pinning the Python ``playwright`` package (requirements-dev.txt) makes "package present but
    Chromium not installed" a realistic state; with the UI server reachable it would otherwise
    throw a cryptic launch error. Narrow on purpose (CHANGELOG "P1 Stage B"): a missing binary
    is a sanctioned skip, so we match the install-hint signature rather than swallowing every
    launch failure — any other error must surface loud.
    """
    msg = str(err).lower()
    return "playwright install" in msg or "executable doesn't exist" in msg


def _isolated_page_session(browser):
    """Yield a page in a FRESH, isolated browser context; close only that context afterwards.

    Each test gets its own ``browser.new_context()`` — a clean cookies/storage/cache jar — so
    tests stay order-independent and one test's UI state cannot leak into the next (identical
    isolation to launching a browser per test). The shared session browser (see ``_pw_browser``)
    is left alive. Extracted from ``pw_page`` so the isolation contract is unit-testable without
    a live browser — see ``tests/test_browser_path_selector_fixture_scope.py``.
    """
    context = browser.new_context(**_context_kwargs())
    try:
        yield context.new_page()
    finally:
        context.close()


@pytest.fixture(scope="session")
def _pw_browser():
    """Session-scoped Chromium — launched once for the whole module run.

    Server reachability is checked here (before launch) so a down server skips every browser
    test without paying a Chromium launch; pytest caches the skip across the session. The probe
    is tri-state: a wrong explicit AUTODEV_UI_E2E_TOKEN FAILS (an operator config error, not an
    environmental absence), a token-protected server without the opt-in skips with the
    actionable hint, and a down server skips exactly as before. A missing browser binary
    degrades to a loud, actionable skip (run: playwright install chromium); any other launch
    failure stays loud. Per-test isolation lives in ``pw_page``, not here.
    """
    probe = _probe_server()
    if probe == "auth_rejected":
        if _E2E_TOKEN:
            pytest.fail(
                f"dashboard at {URL} rejected AUTODEV_UI_E2E_TOKEN (HTTP 401/403) — "
                "wrong or rotated token"
            )
        pytest.skip(
            f"AutoDev UI at {URL} is token-protected (HTTP 401/403) — "
            "set AUTODEV_UI_E2E_TOKEN to run these tests"
        )
    elif probe != "ok":
        pytest.skip(f"AutoDev UI not reachable at {URL} (start uvicorn on port 18790)")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except sync_api.Error as e:  # Playwright's base error, re-exported in sync_api
            if _is_missing_browser_error(e):
                pytest.skip("Chromium not installed for Playwright — run: playwright install chromium")
            raise  # any other launch failure stays loud
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def pw_page(_pw_browser):
    """Function-scoped page in a fresh, isolated browser context (see ``_isolated_page_session``)."""
    yield from _isolated_page_session(_pw_browser)


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
        list_req = urllib.request.Request(URL + "/api/queue", headers=_auth_headers())
        with urllib.request.urlopen(list_req, timeout=10) as resp:
            entries = json.loads(resp.read().decode())
        for entry in entries.get("queue", []):
            if entry.get("project_path") == proj:
                req = urllib.request.Request(
                    URL + f"/api/queue/{entry['id']}",
                    headers=_auth_headers(),
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
            headers={"Content-Type": "application/json", **_auth_headers()},
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
        pw_page.locator("#preflight-repo-path").click()  # focus to open the recents dropdown
        recents = pw_page.locator('[data-testid="server-path-recents"]')
        recents.first.wait_for(state="visible", timeout=10000)
        assert recents.locator("button", has_text=proj).count() >= 1, (
            "the preflighted project must appear in the recents dropdown"
        )
    finally:
        # Remove tmpdir so prune sweeps the recents entry. Best-effort: a
        # cleanup failure must not mask a real test failure.
        shutil.rmtree(base, ignore_errors=True)
        try:
            prune_req = urllib.request.Request(
                URL + "/api/setup/recent-projects/prune",
                data=b"",
                headers=_auth_headers(),
                method="POST",
            )
            urllib.request.urlopen(prune_req, timeout=10).close()
        except Exception:
            pass
