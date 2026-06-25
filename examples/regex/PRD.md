# Regex Tester (Live Highlight)

> **NOTE:** This is the canonical PRD for `regex-tester`. All changes must follow the Change-Request workflow.

## Problem Statement

Developers, data engineers, and analysts iterate on regular expressions constantly — extracting fields, validating input formats, scrubbing logs — but the feedback loop is slow and fragmented. They either run a pattern against a file in a shell, paste into a third-party site of unknown provenance, or guess and re-run. The shell loop hides *which* spans matched; third-party sites send test data (often containing real PII, secrets, or internal log lines) to a remote server. The result is wasted iteration time and an avoidable data-exfiltration surface.

This component solves both: a fully client-side regex tester that highlights matches in the test string in real time as the pattern is typed, with no data ever leaving the browser. Affected users are anyone who authors or debugs regexes; the impact is faster iteration and the removal of a privacy/compliance risk for teams handling sensitive test data.

## Goals & Success Metrics

1. **Goal 1:** Provide a sub-perceptible feedback loop between editing a pattern and seeing matches.
   - **Metric:** Highlight repaint completes in under 50ms at p95 for a 10k-character test string on baseline hardware.
2. **Goal 2:** Eliminate the data-exfiltration surface inherent to hosted regex tools.
   - **Metric:** Zero outbound network requests after initial asset load, verifiable via network panel (enforced by a strict CSP / no `fetch`/`XHR` in app code).
3. **Goal 3:** Make regex flags self-documenting so users do not need an external reference.
   - **Metric:** Every flag control exposes a full label and an on-hover/-focus helper describing its effect; 0 bare single-character controls in the shipped UI.

## User Stories

- **As a** backend developer, **I want** matches to highlight live as I type the pattern, **so that** I can converge on a correct expression without manually re-running it.
- **As a** data analyst handling production logs, **I want** all evaluation to happen locally in my browser, **so that** I can test against sensitive sample data without it leaving my machine.
- **As an** occasional regex user, **I want** each flag button to state what it does and explain itself on hover, **so that** I can choose flags correctly without looking up the syntax.
- **As a** user working in a dim environment, **I want** the app to follow my system light/dark preference and let me override it, **so that** the interface is comfortable and the highlight color stays legible in either theme.
- **As a** user with an invalid pattern mid-edit, **I want** a clear, non-destructive error message, **so that** I understand what is wrong without losing my test string or pattern.

## Functional Requirements

### In Scope (MVP)

- The system **shall** evaluate the pattern against the test string and visually highlight every match span in place, updating on each pattern or test-string keystroke.
- The system **shall** apply the global (`g`) flag implicitly and surface it as a fixed, read-only indicator in the pattern delimiters.
- The system **shall** display a live match count (e.g. "3 matches" / "no matches") reflecting the current pattern and flags.
- The system **shall** render a list of matches showing each match's start index and matched text, capped at 30 entries with an overflow indicator ("+N more") when exceeded; the headline count **shall** always reflect the true total.
- The system **shall** catch invalid-pattern errors and display the engine's error message inline without clearing the pattern or test-string fields.
- The system **shall** treat zero-length matches safely, advancing the scan position to prevent an infinite loop.
- The system **shall** keep the highlight overlay perfectly aligned with the editable test string under scroll, wrapping, and resize.

### In Scope (V1)

- The system **shall** present each optional flag (`i`, `m`, `s`) as a toggle control with a **full text label** (e.g. "Ignore case", "Multiline", "Dotall") rather than a bare single character, with the flag letter shown as secondary text.
- The system **shall** show a helper label (tooltip via `title` plus an accessible description) on hover and on keyboard focus for each flag control, describing the flag's effect in plain language (see Glossary for canonical copy).
- The system **shall** support light and dark themes, defaulting to the user's `prefers-color-scheme` and offering an explicit in-app toggle that overrides the system default for the session.
- The system **shall** provide a one-click control to clear the pattern field, returning focus to it afterward; it **shall not** clear the test string field.
- The system **shall** offer a "copy match list" action that copies the index/value pairs to the clipboard in plain-text format (`index: matched text`), one entry per line.

### Out of Scope / Deferred

- **Persist last pattern, flags, and theme override across sessions** — deferred to a future version (see Open Questions on storage scope). The MVP and V1 roadmap do **not** include state persistence.
- **Display capture-group breakdowns per match** — deferred to a future version beyond V1. The conversion pipeline **shall not** generate phases for this feature.

> **NOTE:** Requirements in the "In Scope (MVP)" section are MVP-complete. Requirements in "In Scope (V1)" are V1 targets. Requirements in "Out of Scope / Deferred" are explicitly excluded and must not be scheduled.

## Edge Cases

- If the pattern field is empty, the system **shall** clear all highlights, the match count, and the match list, leaving the test string fully legible (no transparent-text artifacts).
- If the pattern is syntactically invalid, the system **shall** render the test string normally (no highlights) and surface the error inline; it **shall not** throw or blank the UI.
- If a pattern produces a zero-length match (e.g. `a*`, `^`, `\b`), the system **shall** advance past the position to avoid an infinite loop and **shall not** render zero-width highlight artifacts.
- If the match count exceeds the list cap (30), the system **shall** render the first 30 with an accurate overflow count while keeping the true total in the headline.
- If a catastrophically backtracking pattern is entered against a large test string, the system **shall** evaluate on a debounced/idle boundary and **shall** remain responsive (see NFR: Performance); a hard evaluation-time guard is tracked as an Open Question.
- If the test string contains HTML-significant characters (`<`, `>`, `&`), the system **shall** escape them in the highlight overlay so they render as literal text, never as markup.

## Non-Functional Requirements

- **Performance:** Highlight repaint **shall** complete in under 50ms at p95 for a 10k-character test string; pattern evaluation **shall** be debounced or scheduled on idle to keep typing latency imperceptible.
- **Privacy:** The system **shall not** transmit the pattern or test string to any server. All evaluation **shall** occur client-side. After initial asset load there **shall** be zero outbound requests.
- **Security:** Highlight rendering **shall** escape all user-supplied text; the app **shall** ship with a strict Content-Security-Policy disallowing remote connections and inline-script injection of user content.
- **Accessibility:** All controls **shall** be keyboard-operable and screen-reader-labeled; flag helper text **shall** be exposed on focus (not hover only); highlight color **shall** maintain a minimum WCAG AA 4.5:1 contrast ratio against matched text in both light and dark themes.
- **Reliability:** A malformed pattern **shall** never crash or blank the app; the UI **shall** degrade to "no highlights + inline error."
- **Portability:** The app **shall** run as a static single-page web app with no backend, deployable to any static host or opened from `file://`.

## Dependencies & Integrations

- **Upstream:** None. Input is the user-typed pattern and test string.
- **Downstream:** None. Output is rendered in-app; the only egress is an explicit user-initiated clipboard copy.
- **External:** None. There is no external API; the browser's native `RegExp` engine performs all evaluation. This is a **permanent architectural constraint**, not an omission — engine switching (PCRE, RE2, etc.) is out of scope.
- **ERD:** N/A — the component is stateless beyond ephemeral UI state and optional local-only persistence. No relational data model. (If a persistence layer is added per Open Questions, cite the Change-Request ID here.)

### Configuration / Environment

| Name | Type | Purpose | Phase |
| ---- | ---- | ------- | ----- |
| `API_BASE_URL` | N/A | Not required — this is a fully client-side app with no backend. | N/A |

```json
{
  "client_config": {
    "default_flags": { "g": true, "i": false, "m": false, "s": false },
    "match_list_cap": 30,
    "eval_debounce_ms": 80,
    "theme": "system",
    "persistence": "none"
  }
}
```

## Milestones & Timeline

1. **Phase 1: MVP (Target: 2026-06-17)**
   - Live pattern evaluation, in-place highlighting, match count, and aligned overlay.
   - Implicit `g`, toggleable `i`/`m`/`s`, inline error handling, zero-length-match safety.
   - Match list with index/value pairs and overflow cap.
2. **Phase 2: V1 (Target: 2026-06-17)**
   - Full-label flag controls with hover/focus helper labels.
   - Light/dark theming with system default + in-app override.
   - Copy-match-list and clear-pattern actions.
   - **Excluded from V1:** local-only persistence of last pattern/flags/theme (see Functional Requirements Out of Scope).
   - **Excluded from V1:** capture-group breakdown per match (see Functional Requirements Out of Scope).

## Risks & Mitigations

| Risk | Impact | Mitigation Strategy |
| ---- | ------ | ------------------- |
| Catastrophic backtracking on a large test string hangs the tab | High | Debounce/idle-schedule evaluation; investigate an evaluation-time guard or worker-thread offload with cancellation. |
| Highlight overlay drifts out of alignment with the textarea (font/scroll/wrap mismatch) | Medium | Share identical font metrics, padding, line-height, and wrap behavior between overlay and input; sync scroll position on every scroll event. |
| Highlight color fails contrast in one theme | Medium | Define per-theme highlight tokens and verify contrast ratios against matched text in both light and dark. |
| Browser `RegExp` flavor differs from users' target engine (PCRE, RE2, etc.) | Low | State explicitly in-app that evaluation uses the JS/ECMAScript regex flavor. |

## Open Questions

- Should persistence (last pattern/flags/theme) use `localStorage`, or stay fully ephemeral to preserve the strict "nothing is stored" privacy posture? Default assumption: opt-in, local-only, off by default.
- Do we add a hard evaluation-time guard (cancel + warn) for pathological patterns, and if so, does it warrant moving evaluation to a Web Worker?
- Is capture-group display in scope for V1, or a separate component?

## Glossary & Domain Terms

- **Match span:** A contiguous substring of the test string matched by the pattern, identified by start index and length.
- **Implicit `g` (global):** The global flag is always applied so that *all* matches are found and highlighted, not just the first. It is surfaced as read-only and not user-toggleable.
- **`i` — Ignore case:** Helper copy: "Match letters regardless of upper/lower case." Maps to the JS `i` flag.
- **`m` — Multiline:** Helper copy: "Make `^` and `$` match the start and end of each line, not just the whole string." Maps to the JS `m` flag.
- **`s` — Dotall:** Helper copy: "Let `.` also match newline characters." Maps to the JS `s` flag.
- **Zero-length match:** A match consuming no characters (e.g. from `a*`, `^`, `\b`); requires scan-position advancement to avoid an infinite loop.
- **JS/ECMAScript regex flavor:** The dialect implemented by the browser's native `RegExp`; differs in some syntax from PCRE, RE2, and other engines.

## Revision History

| Date | Version | Author | Changes |
| ---- | ------- | ------ | ------- |
| 2026-06-17 | 0.1 | PRD Creator | Initial import and structural cleanup (added Revision History, removed duplicate section artifact). |
| 2026-06-17 | 0.2 | PRD Creator | Clarified clear-pattern behavior: control clears only the pattern field and returns focus; test string is preserved. Added plain-text `index: matched text` clipboard specification. |
| 2026-06-17 | 0.3 | PRD Creator | Defined output scope for "copy match list" action after discussion on clipboard format preferences (plain text, newline-delimited). |
| 2026-06-17 | 0.4 | PRD Creator | Reconciled Milestones Phase 2 with Out-of-Scope list; added explicit callouts for excluded persistence and capture-group features. Updated both milestone targets to 2026-06-17. Verified all requirement indicators use consistent text labels. |
| 2026-06-17 | 0.5 | PRD Creator | Refined match-list overflow wording after confirming user preference for "+N more" vs "...N more". Preserved headline-total behavior. |
| 2026-06-17 | 0.6 | PRD Creator | Expanded WCAG contrast specification in NFR after review; locked minimum ratio at 4.5:1 for both light and dark themes. |
| 2026-06-17 | 0.7 | PRD Creator | Final reconciliation pass. Phase 2 (V1) explicitly references Functional Requirements Out-of-Scope exclusions. Permanent JS-only regex engine constraint reinforced in Dependencies. |

> PRD CONVERSION-READY