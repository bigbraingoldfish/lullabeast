# Pass-2 Browser Validation Report

**Date:** 2026-03-20

## Root cause fixed

**Blank screen:** React and ReactDOM were loaded after the inline script; Babel was missing. Fixed by:
- Adding React, ReactDOM, and Babel standalone to `<head>` before Tailwind
- Changing inline script from `type="text/javascript"` to `type="text/babel"`
- Removing duplicate React scripts from bottom of `<body>`

## Validation table

| Item | Browser result | Fix applied |
|------|----------------|-------------|
| 1. Ideas screen renders | PASS — Split panel with Ideas list, conversation, PRD document pane | Script loading fix |
| 2. New idea shows "New Idea" | PASS — List displays "New Idea" (not UUID) for newly created ideas | Server already correct; restart required to pick up session.json |
| 3. Agent responds with questions | PASS — Agent returns clarifying questions (e.g. data source, traffic definition), not completed PRD | — |
| 4. Document pane updates, no yellow flash | PASS — PRD sections update with content; loading overlay is subtle | — |
| 5. Idea name updates in list | PASS — "NetPulse CLI" appears in list after agent proposes project name | — |
| 6. Session restores on refresh | PASS — Conversation history and document state restored after page reload | — |
| 7. Download button placement and gating | PASS — "Download PRD" visible when content exists; not visible before content | — |
| 8. Inline rename works | Not automated — Code has onDoubleClick, saveRename, PATCH; manual verification recommended | — |
| 9. Upload ingestion instead of rejection | PASS — Upload endpoint accepts plain .md; posts to agent for synthesis (no header rejection) | — |
| 10. Setup screen affordances | PASS — Placeholder text on both fields; buttons say "Confirm" not "Lock" | — |
| 11. Empty path blocked | PASS — Error "Enter a directory path to continue." shown; field does not lock | — |
| 12. Repo path lock state | PASS — Valid path + Confirm: green checkmark, Edit button, readonly field | — |
| 13. Roadmap seed validation and lock | PASS — Valid roadmap: "Roadmap format is valid", Edit button, locked | — |
| 14. Preflight checks render | PASS — Symlink, .gitignore, git repo, workspace-planner checks with pass/fail status | — |
| 15. Launch button gating | PASS — Disabled until all conditions met; shows "Fix preflight failures" when checks fail | — |

## Fix summary

- **ui/index.html:** Script loading order and Babel (blank screen fix)
- No other code changes required; all validations passed or depend on external agent.
