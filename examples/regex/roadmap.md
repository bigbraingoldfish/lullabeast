# Regex Tester (Live Highlight) Roadmap

- [x] `INFRA-E1` | CRITICAL | Scaffold the static single-page app shell with a strict Content-Security-Policy and zero-network posture

  > Test: Build the app, open the produced `index.html` from `file://` and over a static server, observe the page loads and the browser network panel shows zero outbound requests after the initial asset load.

  **Entry Criteria:** Empty project root with no application code present; PRD Portability and Security NFRs are the source of truth for constraints.

  **Exit Criteria:** A buildable static SPA exists with `index.html`, app entry script, and a stylesheet; a `<meta http-equiv="Content-Security-Policy">` (or server header equivalent in the static build) disallows remote `connect-src`/`script-src` and inline-script injection of user content; the app opens from `file://` with no console errors; no `fetch`/`XHR`/`WebSocket` usage exists anywhere in app code.

  **TDD Requirements:**
  - `csp.test.js`: Validates the shipped CSP string disallows `connect-src` to remote origins and forbids `unsafe-inline` for user content injection.
  - `no_network.test.js`: Statically scans bundled app source and asserts there are zero references to `fetch`, `XMLHttpRequest`, `WebSocket`, or `navigator.sendBeacon`.

  **Done Criteria:**
  - [ ] Static SPA builds and opens from both `file://` and a static host with no console errors
  - [ ] CSP forbids remote connections and inline user-content script injection
  - [ ] No network-egress APIs are referenced in app code
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can open the regex tester as a single web page (even by double-clicking the file) and it loads fully with no internet connection.
  - **How we'll check:** Build the app, open `index.html` via `file://`, open the browser network panel, reload, and confirm no requests are made after the initial local asset load; confirm the page renders without console errors.
  - **If this fails, the user sees:** The page does not open, shows a blank screen, or quietly contacts the network when it should stay entirely on their machine.

- [x] `CORE-E1` | CRITICAL | Implement the client-side regex evaluation engine producing match spans with implicit global flag, zero-length-match safety, and invalid-pattern handling

  > Test: Call the engine with pattern `a*`, flags none, against `"aba"`, and confirm it returns all match spans (including zero-length matches) with correct start indices and matched text without hanging, and that an invalid pattern like `(` returns an error result instead of throwing.

  **Entry Criteria:** `INFRA-E1` complete; app entry script and build pipeline exist.

  **Exit Criteria:** A pure evaluation function accepts a pattern string, the optional flag set (`i`/`m`/`s`), and a test string, and returns either an ordered array of `{index, length, text}` spans or a structured error `{message}`; the global (`g`) flag is always applied internally; zero-length matches advance the scan position by one to prevent infinite loops; invalid patterns are caught and returned as the engine's error message; evaluation uses only the native `RegExp` engine.

  **TDD Requirements:**
  - `engine_matches.test.js`: Validates correct spans for literal, multi-match, and flagged (`i`/`m`/`s`) patterns including correct start indices and matched text.
  - `engine_zero_length.test.js`: Validates that `a*`, `^`, and `\b` patterns terminate and yield correct zero-length spans without infinite looping.
  - `engine_errors.test.js`: Validates that syntactically invalid patterns (e.g. `(`, `[`) return a structured error object carrying the engine message and never throw.

  **Done Criteria:**
  - [ ] Engine returns ordered match spans with index, length, and text
  - [ ] Implicit `g` is always applied; zero-length matches never loop forever
  - [ ] Invalid patterns return a structured error rather than throwing
  - [ ] Evaluation uses only the native browser `RegExp` engine
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** When the user types a pattern, the tool finds every place it matches in the test text — including tricky patterns that match nothing — without freezing or crashing.
  - **How we'll check:** Run the engine unit tests covering multi-match, flagged, zero-length, and invalid patterns and confirm all pass with correct spans and graceful errors.
  - **If this fails, the user sees:** The tool freezes on certain patterns, misses matches, or breaks when a pattern is half-typed.

- [x] `CORE-E2` | HIGH | Implement match aggregation: true total count plus a capped display list with overflow accounting

  > Test: Feed the engine output for a pattern producing 45 matches and confirm the aggregator reports a true total of 45, returns exactly 30 list entries, and an overflow value of 15.

  **Entry Criteria:** `CORE-E1` complete; evaluation function returns match spans.

  **Exit Criteria:** An aggregation function takes the span array and returns `{total, entries, overflow}` where `total` is the true match count, `entries` is capped at 30 `{index, text}` pairs, and `overflow` is `total - entries.length` (0 when not exceeded); `match_list_cap` is sourced from client config (30).

  **TDD Requirements:**
  - `aggregate_cap.test.js`: Validates total, capped entry count, and overflow for inputs below, at, and above the 30 cap.
  - `aggregate_empty.test.js`: Validates that zero spans yield total 0, empty entries, and overflow 0.

  **Done Criteria:**
  - [ ] True total is always reported independent of the cap
  - [ ] Display list is capped at 30 entries with accurate overflow
  - [ ] Cap value is read from client config
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user sees an accurate count of how many matches exist even when only the first 30 are listed, with a clear "+N more" indication of the remainder.
  - **How we'll check:** Run aggregation tests with match counts below, at, and above 30 and confirm the reported total, listed entries, and overflow values are correct.
  - **If this fails, the user sees:** A match count that disagrees with the list, or a list that hides matches without saying how many are left out.

- [x] `PERF-E1` | HIGH | Schedule evaluation on a debounced/idle boundary to keep typing responsive on large test strings

  > Test: Drive 20 rapid simulated keystrokes within the debounce window against a 10k-character string and confirm the engine runs once (after the window), and that a measured highlight repaint completes under 50ms at p95.

  **Entry Criteria:** `CORE-E1` and `CORE-E2` complete; evaluation and aggregation functions exist.

  **Exit Criteria:** Pattern/test-string changes trigger evaluation through a debounce/idle scheduler using `eval_debounce_ms` (80) from client config; rapid sequential input coalesces into a single evaluation; a repaint timing harness demonstrates p95 under 50ms for a 10k-character test string on baseline hardware; the scheduler is cancellable so superseded evaluations do not paint stale results.

  **TDD Requirements:**
  - `debounce.test.js`: Validates that N rapid changes within the window result in exactly one evaluation and that a later change after the window triggers a new one.
  - `perf_repaint.test.js`: Validates the repaint timing harness records p95 under 50ms for a 10k-character string across repeated runs.

  **Done Criteria:**
  - [ ] Rapid input coalesces into a single scheduled evaluation
  - [ ] Debounce interval is read from client config (80ms)
  - [ ] Superseded evaluations are cancelled and never paint stale output
  - [ ] p95 repaint under 50ms for a 10k-character string is demonstrated
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** Typing a pattern feels instant — highlights update smoothly even with a very large block of test text, with no stutter or lag.
  - **How we'll check:** Run the debounce and repaint-timing tests against a 10k-character string and confirm single-evaluation coalescing and sub-50ms p95 repaint.
  - **If this fails, the user sees:** The page stutters or freezes while typing against a large body of text.

- [x] `UI-E1` | HIGH | Build the app shell: pattern input with a fixed read-only global-flag indicator and the test-string editor

  > Test: Load the app, confirm a pattern input is rendered flanked by delimiter affordances showing a non-editable `g` indicator, and a multi-line editable test-string field is present and accepts typed text.

  **Entry Criteria:** `INFRA-E1` complete; app shell and stylesheet exist.

  **Exit Criteria:** The UI renders a pattern input field with delimiter affordances that display the implicit global (`g`) flag as a fixed, read-only indicator (not a toggle); a multi-line editable test-string field is present; both controls are keyboard-focusable and screen-reader labeled; no evaluation wiring is required in this phase.

  **TDD Requirements:**
  - `shell_render.test.js`: Validates the pattern input, the read-only `g` indicator, and the editable test-string field are all present in the DOM.
  - `shell_a11y.test.js`: Validates both inputs expose accessible names and are reachable via keyboard tab order.

  **Done Criteria:**
  - [ ] Pattern input renders with a fixed, read-only `g` indicator in the delimiters
  - [ ] Editable multi-line test-string field renders and accepts input
  - [ ] Both controls are keyboard-focusable and screen-reader labeled
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user sees a place to type a regex pattern (with the always-on global flag clearly shown but not editable) and a separate box to paste or type their test text.
  - **How we'll check:** Load the rendered page, confirm the pattern field shows a non-editable `g` marker, type into the test-string field, and confirm both fields are reachable by keyboard.
  - **If this fails, the user sees:** Missing or confusing input fields, or a global-flag marker they can accidentally turn off.

- [x] `UI-E2` | CRITICAL | Render the live highlight overlay aligned to the test-string editor with HTML escaping and correct empty-pattern behavior

  > Test: Type pattern `<a>` against a test string containing `<a> and <a>`, confirm both literal `<a>` spans are highlighted as visible text (not parsed as markup) and the overlay stays aligned while scrolling and resizing; then clear the pattern and confirm all highlights disappear with the test string fully legible.

  **Entry Criteria:** `CORE-E1`, `PERF-E1`, and `UI-E1` complete; evaluation engine and scheduler are wired to the pattern/test-string fields.

  **Exit Criteria:** A highlight overlay is layered over the test-string editor sharing identical font metrics, padding, line-height, and wrap behavior; scroll position syncs on every scroll event and alignment holds under wrapping and resize; all user-supplied text in the overlay is HTML-escaped so `<`, `>`, `&` render as literals; an empty pattern clears all highlights leaving the test string legible with no transparent-text artifacts; zero-length matches render no zero-width highlight artifacts.

  **TDD Requirements:**
  - `overlay_escape.test.js`: Validates that test strings containing `<`, `>`, `&` are escaped in the overlay and rendered as literal text.
  - `overlay_empty.test.js`: Validates that an empty pattern produces no highlight markup and the test string remains fully legible.
  - `overlay_zero_length.test.js`: Validates that zero-length matches produce no zero-width highlight artifacts.

  **Done Criteria:**
  - [ ] Highlights render in place over matched spans and stay aligned under scroll/wrap/resize
  - [ ] All overlay text is HTML-escaped; markup characters render literally
  - [ ] Empty pattern clears highlights with no transparent-text artifacts
  - [ ] Zero-length matches produce no visible artifacts
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** As the user types a pattern, the matching parts of their test text light up exactly over the right characters, even when the text scrolls, wraps, or contains characters like `<` and `&`.
  - **How we'll check:** Type a pattern against text containing markup characters and long wrapped lines, confirm highlights sit precisely over matches and characters render literally, then clear the pattern and confirm highlights vanish cleanly.
  - **If this fails, the user sees:** Highlights drift off the matching text, special characters disappear or break the layout, or stray colored boxes linger after clearing the pattern.

- [x] `UI-E3` | HIGH | Display the live match count and the capped match list with index/value pairs and an overflow indicator

  > Test: Type a pattern matching 45 spans and confirm the headline shows "45 matches", the list shows 30 `index: matched text` entries, and an overflow indicator reads "+15 more"; with a no-match pattern confirm the headline reads "no matches".

  **Entry Criteria:** `CORE-E2` and `UI-E2` complete; aggregation output is available to the UI.

  **Exit Criteria:** A live match count reflects the true total with sensible wording ("3 matches" / "no matches"); a match list renders each displayed match's start index and matched text as `index: matched text`; the list is capped at 30 with a "+N more" overflow indicator when exceeded; the headline always shows the true total even when the list is capped; the list updates live with evaluation.

  **TDD Requirements:**
  - `matchlist_render.test.js`: Validates the list renders `index: matched text` entries and the headline reflects the true total.
  - `matchlist_overflow.test.js`: Validates the "+N more" indicator appears with the correct N when matches exceed 30 and is absent at or below the cap.
  - `matchcount_states.test.js`: Validates singular/plural/"no matches" headline wording.

  **Done Criteria:**
  - [ ] Headline match count reflects the true total in all cases
  - [ ] Match list shows `index: matched text` entries capped at 30
  - [ ] "+N more" overflow indicator is accurate when the cap is exceeded
  - [ ] Count and list update live as the pattern/flags/test string change
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user sees how many matches there are and a readable list of where each match starts and what it captured, with a clear note when there are more than the list shows.
  - **How we'll check:** Enter patterns yielding zero, one, several, and more-than-30 matches and confirm the headline count, the listed index/value pairs, and the overflow note are all correct.
  - **If this fails, the user sees:** A wrong or missing match count, an unreadable match list, or a list that silently drops matches.

- [x] `UI-E4` | HIGH | Surface invalid-pattern errors inline without clearing the pattern or test-string fields

  > Test: Type an invalid pattern such as `(` and confirm the engine's error message appears inline, the test string renders normally with no highlights, and both the pattern and test-string field contents are preserved.

  **Entry Criteria:** `CORE-E1` and `UI-E2` complete; the engine returns structured errors and the overlay is wired.

  **Exit Criteria:** When evaluation returns an error, the engine's message is displayed inline near the pattern field; the test string renders normally with no highlights and the UI does not blank or crash; the pattern and test-string field values are preserved unchanged; the error region is screen-reader announced and clears automatically once the pattern becomes valid.

  **TDD Requirements:**
  - `error_inline.test.js`: Validates that an invalid pattern renders the engine message inline while preserving both field values.
  - `error_recovery.test.js`: Validates that correcting the pattern clears the inline error and restores highlighting.

  **Done Criteria:**
  - [ ] Invalid patterns show the engine error inline without clearing fields
  - [ ] The test string renders normally with no highlights during an error
  - [ ] The UI never blanks or crashes on a malformed pattern
  - [ ] The error clears automatically when the pattern becomes valid
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** When the user types a pattern that is not yet valid, a clear message explains the problem and nothing they typed is lost.
  - **How we'll check:** Type an invalid pattern, confirm the inline error appears and both fields keep their content, then complete the pattern and confirm the error disappears and highlighting resumes.
  - **If this fails, the user sees:** The app goes blank, crashes, or wipes out their pattern or test text when a pattern is mid-edit.

- [x] `UI-E5` | MEDIUM | Add labeled optional-flag toggles (`i`/`m`/`s`) with full text labels and accessible hover/focus helper descriptions

  > Test: Confirm each of the `i`, `m`, `s` controls shows a full label ("Ignore case", "Multiline", "Dotall") with the flag letter as secondary text, exposes the glossary helper copy via `title` and an accessible description on both hover and keyboard focus, and that toggling a flag re-evaluates and updates highlights.

  **Entry Criteria:** `UI-E2` and `UI-E3` complete; evaluation accepts the optional flag set.

  **Exit Criteria:** Each optional flag (`i`, `m`, `s`) is a toggle with a full text label and the flag letter shown as secondary text — no bare single-character controls; each control exposes the canonical glossary helper copy via `title` plus an accessible description (`aria-describedby`) surfaced on hover and keyboard focus; toggling a flag re-runs evaluation and updates highlights, count, and list; all flag controls are keyboard-operable and screen-reader labeled.

  **TDD Requirements:**
  - `flags_labels.test.js`: Validates each flag control renders its full label and secondary letter and that no bare single-character control exists.
  - `flags_helpers.test.js`: Validates the glossary helper copy is present via `title` and an accessible description exposed on focus, not hover only.
  - `flags_toggle.test.js`: Validates toggling each flag changes evaluation results (e.g. `i` makes a match case-insensitive).

  **Done Criteria:**
  - [ ] `i`/`m`/`s` render as full-label toggles with the letter as secondary text
  - [ ] Zero bare single-character flag controls ship in the UI
  - [ ] Glossary helper copy is exposed on both hover and keyboard focus accessibly
  - [ ] Toggling a flag re-evaluates and updates highlights, count, and list
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user sees clearly named flag buttons ("Ignore case", "Multiline", "Dotall") that explain what they do on hover or keyboard focus, and turning one on immediately changes which text is highlighted.
  - **How we'll check:** Hover and tab to each flag control to confirm the explanatory helper text appears, then toggle each flag and confirm the matches update accordingly.
  - **If this fails, the user sees:** Cryptic single-letter buttons with no explanation, or flags that do not change the results when toggled.

- [x] `UI-E6` | MEDIUM | Implement light/dark theming with system default, in-app override, and per-theme highlight contrast

  > Test: With the OS set to dark, confirm the app loads dark; toggle the in-app control to light and confirm it overrides for the session; in both themes confirm the highlight color maintains at least WCAG AA 4.5:1 contrast against matched text.

  **Entry Criteria:** `UI-E2` complete; highlight rendering exists so theme tokens can be applied to it.

  **Exit Criteria:** The app defaults to the user's `prefers-color-scheme`; an explicit in-app toggle overrides the system default for the current session (no persistence); per-theme highlight color tokens are defined and verified to meet a minimum WCAG AA 4.5:1 contrast ratio against matched text in both light and dark; the theme toggle is keyboard-operable and screen-reader labeled.

  **TDD Requirements:**
  - `theme_default.test.js`: Validates the initial theme follows the `prefers-color-scheme` media query.
  - `theme_override.test.js`: Validates the in-app toggle overrides the system default for the session.
  - `theme_contrast.test.js`: Validates the highlight-vs-text contrast ratio is >= 4.5:1 in both light and dark themes.

  **Done Criteria:**
  - [ ] Theme defaults to the system `prefers-color-scheme`
  - [ ] In-app toggle overrides the system default for the session (no persistence)
  - [ ] Highlight color meets WCAG AA 4.5:1 against matched text in both themes
  - [ ] Theme toggle is keyboard-operable and screen-reader labeled
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The app matches the user's system light/dark setting on load, lets them flip it manually, and keeps highlighted matches clearly legible in either mode.
  - **How we'll check:** Simulate dark and light system preferences to confirm the default, use the in-app toggle to override, and measure highlight-to-text contrast in both themes against the 4.5:1 threshold.
  - **If this fails, the user sees:** A theme that ignores their system setting, an override toggle that does nothing, or highlights that are hard to read in one of the themes.

- [x] `UI-E7` | MEDIUM | Add the clear-pattern control and the copy-match-list action

  > Test: With a pattern and test string present, click clear-pattern and confirm the pattern field empties, keyboard focus returns to it, and the test string is untouched; then click copy-match-list and confirm the clipboard holds one `index: matched text` line per displayed entry.

  **Entry Criteria:** `UI-E1` and `UI-E3` complete; the pattern field and match list are rendered.

  **Exit Criteria:** A one-click control clears only the pattern field and returns focus to it, leaving the test-string field unchanged; a "copy match list" action copies the displayed index/value pairs to the clipboard as plain text, one `index: matched text` entry per line; both controls are keyboard-operable and screen-reader labeled; clipboard write uses the local Clipboard API only (no network).

  **TDD Requirements:**
  - `clear_pattern.test.js`: Validates the clear control empties only the pattern field, preserves the test string, and returns focus to the pattern field.
  - `copy_matchlist.test.js`: Validates the copied clipboard text is newline-delimited `index: matched text` pairs matching the displayed entries.

  **Done Criteria:**
  - [ ] Clear-pattern empties only the pattern field and refocuses it
  - [ ] Clear-pattern never alters the test-string field
  - [ ] Copy-match-list writes newline-delimited `index: matched text` plain text to the clipboard
  - [ ] Both controls are keyboard-operable and screen-reader labeled
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can clear just the pattern with one click (their test text stays put and the cursor lands back in the pattern box) and copy the whole match list as plain text to paste elsewhere.
  - **How we'll check:** Click clear-pattern and confirm only the pattern empties with focus returned, then click copy-match-list and confirm the clipboard contains one `index: matched text` line per shown match.
  - **If this fails, the user sees:** A clear button that also wipes their test text, or a copy action that produces nothing or the wrong format.