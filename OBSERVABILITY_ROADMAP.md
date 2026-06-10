# Lullabeast Observability Roadmap

**Status:** Audit complete (2026-06-09). Audit-only deliverable — no code changed.
**Scope:** Data capture gaps (Part A), Queue screen observability (Part B), Pipeline screen observability (Part C), synthesized into a dependency-ordered phased roadmap.
**Method:** Every claim below was verified against source. Citations use `file:function` (durable) plus line numbers as of commit `606c0ff`.

Classification used throughout:

| State | Meaning |
|-------|---------|
| **S1** | Captured and surfaced in the UI |
| **S2** | Captured/persisted but not surfaced (or surfaced only partially) |
| **S3** | Not captured |

---

## 0. Persistence Surface Inventory

What exists today, who writes it, who reads it:

| Artifact | Writer | Reader(s) | Retention |
|----------|--------|-----------|-----------|
| `pipeline_events.jsonl` | `orchestrator._write_pipeline_event` (orchestrator.py:696) — **orchestrator only**; the UI server never appends | `ui/server.py:_poll_pipeline_events_file` → SSE `/api/events/stream`, `/api/events` backlog | **Unbounded** — no rotation. Server tail self-heals on truncation (server.py:224) |
| `metrics.jsonl` (per project) | `orchestrator._write_canonical_metrics_row` (~5031), one row per completed phase, on reviewer-PASS before phase_state deletion | `/api/metrics-summary`, `/api/queue` enrichment (`_project_metrics_totals`), reviewer gate | Dedup **keep-last-per-phase** on read; agent-writable (untrusted) |
| `metrics_history/<project>.jsonl` | Same writer, orchestrator-private append-only mirror | **Nothing reads it today** (rebuild source only) | Append-only, unbounded |
| `run_summary.json` + `runs_index.jsonl` | `orchestrator._write_run_summary` (878) at terminal exits: PIPELINE_COMPLETE (4362, 5435), BLOCKED (4389, 5450), HALTED_SILENT (5652, 7301), STOPPED (5705, 7448, 7470) | `GET /api/metrics-global` (server.py:3720) — **no UI consumer** (docstring: "W4-H deferred") | One file per project (overwritten per run); index unbounded |
| `phase_state.json` | Orchestrator: retry counters, blame, tokens accumulators, outcome fields, escalation advisory fields | Server `_compute_escalation_view` (subset), gates, metrics row writer | Deleted on phase advance; archived to pipeline-audit on success |
| `pipeline_state.json` | `transition_state`/`write_state`; external IDLE resets | `GET /api/state` (explicit whitelist, server.py:~2570) | Single file, overwritten |
| `pipeline_queue.json` | Server (add/reorder/parent/mode) + orchestrator (ACTIVE/park/COMPLETED/FAILED) via version-CAS | `GET /api/queue`, `/api/queue/{id}/snapshot` | Single file |
| `{agent}_activity.stamp` | OpenClaw plugin (model_call_started/ended, after_tool_call) — mtime-only, empty file | `poll_for_sentinel` stall detection only — **no API exposure** | Overwritten |
| Agent output artifacts (`planner_output.json`, `executor_output.json`, `reviewer_output.json`, `gate_warnings.json`, `executor_gate_detail.json`, `executor_advisory_detail.json`, `failure_context.json`) | Agents + gates | Gates, orchestrator drain paths; snapshot probes check **existence** only | Per-phase lifecycle; archived on success |
| Pipeline-audit archive | Orchestrator on phase success (~6859): `current_phase.json`, `phase_state.json`, 3 agent outputs, `metrics.jsonl` | Nothing automated | Unbounded; success-only (no archive on escalation/failure) |
| `session_cleanup.log` | `session_cleanup.py` (RotatingFileHandler 5MB) | Nothing | Rotated |
| OpenClaw session JSONLs | OpenClaw gateway | `orchestrator._sum_session_tokens` (622) per attempt | 30-day TTL prune |

---

## Part A — Data Capture Gaps

### Q1. Which agent or stage causes the most failures and retries?

**Verdict: mostly captured (S1/S2), with three consistency gaps.**

What exists: per-phase lifetime counters in `phase_state` → canonical metrics row (`executor_attempts`, `executor_self_failures`, `executor_reviewer_rejections`, `reviewer_passes`, `blame_fires`, `escalations`, `reviewer_unverified_retries`, `escalation_resets`, `nuclear_resets`, `reset_log`); per-attempt `gate_fail` / `attempt_end` / `poll_outcome` events with `retry_class` ∈ {initial_attempt, executor_self_failure, reviewer_rejection}; `blame_verdict` ∈ {plan, impl, infra, unknown} on the metrics row and `blame_attributions` in `run_summary.json`.

| Data point | Capture location | Schema note | Audience | State | Serves |
|---|---|---|---|---|---|
| Per-attempt failure cause by agent | `gate_fail` events (orchestrator.py:5907 planner, 6300 executor, ~6621 reviewer) | **Planner `gate_fail` omits `retry_class`** (5907–5915: only `exit_code`, `last_error_code`); executor always carries it (6312); reviewer conditionally | Internal (feed renders it) | S2 | Q1 |
| Run boundary for "failures per run" grouping | Nowhere — events carry `project`/`phase` but **no `run_id`** | Reconstructing runs requires joining `run_started_at` (pipeline_state) against event `ts` — fragile across re-runs of the same project | Internal | **S3** | Q1, Q3, Q4, Q5 |
| Per-stage failure counts in the summary endpoint | Metrics row has them; `/api/metrics-summary` per-phase rows (server.py:3656–3682) pass through attempts/self_failures/rejections but **drop** `gate_warnings`, `reachability_summary`, `reset_log`, `escalation_resets`, `nuclear_resets`, `reviewer_unverified_retries` | Read-and-dropped: writer persists pain signals expressly for analysis (CLAUDE.md "pain-signal fields"); reader discards them | User-facing once passed through | S2 | Q1 |
| Planner lifetime retry split | Only per-segment `planner_retries` (reset on `reset_phase`) | No planner analogue of the executor's lifetime self-failure/rejection split — acceptable (planner has no rejection loop) but note for symmetry | Internal | S2 | Q1 |

### Q2. Which project types / PRD characteristics correlate with run success vs failure?

**Verdict: outcome side half-captured; characteristics side not captured (S3).**

What exists: `run_summary.json` carries `outcome`, `outcome_detail`, `phases_attempted`, `executor_attempts_total`, `escalations_total`, `blame_attributions`, `skills_injected`, `token_usage`, `idea_id`, per-phase `{phase, executor_attempts, blame, skill_used, last_error_code, escalation_trigger_reason}`. `runs_index.jsonl` gives the cross-run spine. `GET /api/metrics-global` already computes per-project run counts, `last_outcome`, `avg_executor_attempts`, `escalation_rate`, and a `skill_vs_no_skill_executor_attempts` comparison (server.py:3789–3812).

| Data point | Capture location | Schema note | Audience | State | Serves |
|---|---|---|---|---|---|
| PRD/roadmap characteristics (phase count at start, subsystem-prefix mix CORE/UI/API…, BV-block density, roadmap size) | Computed live and discarded: `_roadmap_phase_checkbox_stats` (server.py:6393) for display; `phase_resolver` parses per-phase only | **Nothing snapshots roadmap shape into the run record.** Correlation analysis has no left-hand side | Internal (analysis) | **S3** | Q2 |
| Run outcome for escalation-parked / FAILED-spawn projects | `_write_run_summary` fires only on the 4 terminal `pipeline_status` values | A project parked on `ESCALATION` (queue advances) or marked queue-`FAILED` at spawn **never gets a run_summary** → success/failure dataset is survivorship-biased | Internal | **S3** (for these outcomes) | Q2 |
| Cross-project aggregates | `GET /api/metrics-global` (server.py:3720) | Endpoint exists, correct, and **dark** — zero references in `ui/index.html` | Would be user-facing | S2 | Q2 |
| Idea→project linkage | `run_summary.idea_id`, queue entry `idea_id` | Captured; never joined/surfaced | Internal | S2 | Q2 |

### Q3. Per-stage token consumption and latency distribution

**Verdict: per-phase-per-role captured well; per-attempt and live mid-phase partially missing.**

What exists: `_sum_session_tokens` (orchestrator.py:622–693) reads the **verified** nested OpenClaw shape (`message.usage.{input,output,cacheRead,cacheWrite,totalTokens,cost.total}` — the historic field-name-mismatch bug is fixed and documented in the docstring). `_accumulate_role_tokens` (~3344) runs **after every agent attempt**, accumulating into `phase_state.{planner,executor,reviewer}_tokens_acc` with keys `{input, output, cache_read, cache_write, total_tokens, cost_total}`. Metrics row persists the three role dicts + `cost_total` + `duration_seconds`; `/api/metrics-summary` aggregates run/role/phase tokens & cost (verified writer/reader key match: `total_tokens`/`cost_total` both sides — server.py:3357–3395). Per-attempt **latency** exists (`attempt_end.detail.duration_s`, `poll_outcome.duration_s`).

| Data point | Capture location | Schema note | Audience | State | Serves |
|---|---|---|---|---|---|
| Live (mid-phase) token/cost accumulators | `phase_state.{role}_tokens_acc`, updated per attempt | **No API exposes them** — zero greps for `tokens_acc` in server.py. UI's "Run so far" line covers completed phases only | Would be user-facing | S2 | Q3, Part C |
| Per-attempt token/cost delta | Not persisted — `attempt_end` detail = `{reason, duration_s, attempt, session_key, retry_class}` only; accumulator stores running sum, delta is discarded | The data is in hand at the accumulation call site; emitting it is one event-field addition | Internal→feed | **S3** | Q3, Q1 |
| Token-capture failure signal | `_sum_session_tokens` returns **silent zeros** on missing/unreadable session file (622:651–656, stdout WARN only) | Same *symptom* as the historic `_sum_session_tokens` bug (zeroed cost), different cause; no event, no phase_state flag — a renamed sessions dir would zero all cost silently again | Internal | **S3** | Q3 integrity |
| Per-model-call latency / tool-call telemetry | Plugin handles `model_call_started/ended`, `after_tool_call` but writes **mtime-only stamps**; tool names, per-call usage, error payloads, `agent_end.success` are discarded (plugin/src/stall-detector.ts, agent-end-handler.ts) | Untapped live telemetry; any capture must not break the empty-file stamp contract | Internal | **S3** | Q3, Part C "current action" |
| Hold vs active time | `_derive_hold_seconds_per_phase` (server.py:3501) pairs `escalation_trigger`→`escalation_resolve` events; surfaced as `total_hold_seconds`/`total_active_seconds` + per-phase `hold_seconds` (index.html:2265, 2370) | Working as designed | User-facing | S1 | Q3, Q4 |

### Q4. Where do human interventions occur (and are escalation-panel actions logged as intervention events)?

**Verdict: the orchestrator-consumed half is logged; the server-side half is not.**

What exists: `escalation_resolve` event with `{command}` when the orchestrator consumes an answer; `escalation_command_invalid`; `queue_revived` with the banked `{command}`; `reset_log` audit trail in phase_state→metrics row; `escalation_output.json` / `pending_escalation_command.json` written by the server with `{command, source:"ui", timestamp}` (server.py:2776–2848) — but these files are **consumed/overwritten**, not a log.

| Data point | Capture location | Schema note | Audience | State | Serves |
|---|---|---|---|---|---|
| Escalation-panel command issuance | `POST /api/command` → `_write_escalation_files` | Durable trace exists **only if** the orchestrator consumes it (`escalation_resolve`); a banked-then-superseded command leaves no record | Internal | S2 (lossy) | Q4 |
| Stop / resume-ready / resume-orchestrator / git-recover | server.py:6281, 3060, 3185, 3012 | **No event written by any of them.** Stop is a payload-less sentinel file; resumes/recover are bare state rewrites; only trace is file mtime + orchestrator spawn log | — | **S3** | Q4 |
| Queue mutations (add, delete, reorder, parent set/clear, clear, mode toggle, relaunch, revalidate) | `/api/queue/*` handlers | No audit/event of operator action; only resulting entry state. `pipeline_events.jsonl` has **one writer** (orchestrator) today | — | **S3** | Q4 |
| Automated interventions (heartbeat restarts) | `heartbeat_cron.py` run_heartbeat — restart decision logged to **stdout only** | Auto-recovery frequency is unanswerable from durable data | — | **S3** | Q4 |
| Time-waiting-on-human | `waiting_for_human_at` (orchestrator.py:7230, 5605) + `answered_at` (queue) + hold-seconds derivation | Captured and surfaced (ElapsedTimer, hold chips) | User-facing | S1 | Q4 |

### Q5. Escalation activation frequency and trigger pattern (which gate fired)

**Verdict: frequency yes; pattern no — the trigger is free text.**

What exists: `escalation_trigger` event per activation; `escalations` counter per phase; `escalation_resets`/`nuclear_resets`; `last_error_code` (machine-readable ERR_* from gates, see executor_gate.py/reviewer_gate.py inventories); `last_poll_reason` ∈ {succeeded, stalled, no_first_activity, stopped, timeout}.

| Data point | Capture location | Schema note | Audience | State | Serves |
|---|---|---|---|---|---|
| Trigger classification | orchestrator.py:7224: `_ps["escalation_trigger_reason"] = self.state.get("last_action", …)` — **a copy of the free-text `last_action` narrative**; event detail = `{reason: <that string>}` (7240) | Distinguishing "elapsed-time stall" vs "consecutive gate failures" vs "provider rejection" requires string-parsing prose (the UI's `humanizeSummary` already does fragile keyword matching on it). `last_error_code` and `last_poll_reason` exist in phase_state at trigger time but are **not joined into the event** | Internal + UI | S2 (frequency) / **S3** (structured pattern) | Q5 |
| Advisory layer outcome | `escalation_advisory_status` ∈ {generating, ready, fallback}, `escalation_headline`, `escalation_message`, `escalation_recommended_action` (orchestrator.py:2692–2696, 7227–7237; surfaced via `_compute_escalation_view`) | Captured and surfaced in the panel. Advisory text is **not** persisted to metrics/run_summary — no post-hoc "was the advisory right?" analysis | User-facing | S1 (live) / S2 (historical) | Q5 |
| Escalation outcome pairing (trigger→command→result) | Derivable from events (`escalation_trigger`…`escalation_resolve`…next outcome) but requires run_id-less event archaeology | See Q1 run_id gap | Internal | S2 | Q4, Q5 |

### Integrity findings (the `_sum_session_tokens` bug class)

1. **Silent-zero token capture** (S3 signal): missing session JSONL → zeros + stdout WARN (orchestrator.py:651–656). No event/flag. *Same observable failure as the historic field-name bug.*
2. **Dual event schema, normalized on one path only**: `/api/events` backlog normalizes `ts|timestamp`, `event_type|event` (server.py:243–244); the SSE file path forwards raw orchestrator lines. The frontend compensates (`event.event_type || event.event`, index.html EventRow). Works, but the contract lives in three places — codify before adding writers.
3. **`/api/metrics-summary` drops persisted pain-signals** (gate_warnings, reachability_summary, reset_log, per-phase reset counters) that the writer added specifically for analysis.
4. **`/api/metrics-global` is dark** — implemented, documented "no in-release UI consumer", never fetched.
5. **`pipeline_events.jsonl` grows unbounded**; the dedup'd metrics dataset and hold-derivation both depend on scanning it.
6. **phase_state outcome fields unexposed**: `last_poll_reason`, `last_attempt_summary`, `last_abort_result`, `last_phase_outcome`, `last_gate_warnings` are written (Section 6.4) and read by nothing in server.py.
7. **Planner `gate_fail` missing `retry_class`** (inconsistent with executor/reviewer).
8. **Queue lifecycle timestamps incomplete**: entries carry `added_at` (server.py:1607/8154/9369), `started_at`, `completed_at` (orchestrator.py:4367/5439/5689), `blocked_at` (4394/5455), `failed_at` (5656/7305/7532), `parked_at`, `answered_at`, `preflight_validated_at` — but **no generic `state_changed_at`**, so time-in-state for `READY`, `DEPENDENCY_HOLD`, `SKIPPED_PENDING` is approximated by `added_at` and goes stale after re-transitions.
9. **Webhook invocation outcomes** (`SUCCESS|AUTH_ERROR|REQUEST_ERROR|INFRA_ERROR`, retries, idempotencyKey) are stderr-logged only (webhook_client.py:173–206) — infra-flakiness frequency is unanswerable.
10. **Audit archive is success-only**: escalated/abandoned phases never archive their agent outputs, exactly the runs you'd want to study.

---

## Part B — Queue Screen Observability

Current rendering (verified in `ui/index.html`): rows show name/path, status pill (`queueRowDisplay`), phase (`current_phase_raw_id` + `parked_agent`), progress (`phases_complete/phases_total`), elapsed (state-dependent source), cost (`cost_total`), rank. Expansion (`QueueProjectSnapshot` + `QueueActionHub`) adds kind-specific stat cards, escalation advisory, dependency line (`isWaitingForParent`), live activity (agent, attempt counts, `last_action` 60-char, ElapsedTimer), skill labels. Filter chips: running / attention / queued / complete (`queueEntryBucket`, index.html:710).

Proposals (format: **data point | display location | trigger state | display pattern**):

| # | Data point | Display location (component) | Trigger state | Display pattern |
|---|---|---|---|---|
| B1 | Needs-input vs pipeline-failure distinction | Filter chips + status pill (`queueEntryBucket`, `queueRowDisplay`) | Entries in today's `attention` bucket | Split `attention` into **Needs input** (ESCALATION, ESCALATION_ANSWERED, ACTIVE+WAITING_FOR_HUMAN) and **Failed/Blocked** (FAILED, BLOCKED, ACTIVE+HALTED_SILENT). Amber vs red chip; pill copy already differs — make the bucket match the mental model. UI-only; all inputs already in the payload (`state`, `live_pipeline_status`, `has_banked_answer`) |
| B2 | Time-in-state for *every* row | ELAPSED column (currently "—" for queued/held rows) + DEP badge tooltip | READY, DEPENDENCY_HOLD, SKIPPED_PENDING (states with no stamp today) | "queued 2h" / "held 45m" from new `state_changed_at` (Phase 3 schema add). Existing rows keep current sources (started_at/parked_at/failed_at) |
| B3 | Dependency hold duration + parent progress | `QueueProjectSnapshot` dependency line ("Waiting for: X") | Any incomplete parent (`isWaitingForParent`) | Append parent's `phases_complete/phases_total` and "held Xm" — turns "waiting" into "waiting, parent 3/7, ~40 min in" |
| B4 | Last agent activity (true liveness) | Live-activity block in `QueueProjectSnapshot` (next to last_action) | ACTIVE rows with live orchestrator | "agent active 8s ago" from `{agent}_activity.stamp` mtime age (exposed via Phase 2 server work); grey "no signal Xm" when stale — distinguishes working-agent from wedged-agent without opening logs |
| B5 | Estimated cost/tokens for queued projects | COST column + QUEUED stat card | READY/SKIPPED_PENDING rows with `cost_total == null` | "~$4.20 est" (muted, tilde prefix): `phases_total × median per-phase cost` from same-project `metrics_history`, falling back to cross-project medians via `runs_index`/run summaries; suppressed below a history floor (Phase 4) |
| B6 | Prior-run history for this project | `QueueProjectSnapshot` footer line | Any row whose project has `runs_index` entries | "2 prior runs · last: BLOCKED (Jun 5)" from `/api/metrics-global` per-project rows — currently dark data (Phase 4) |
| B7 | Failure reason on FAILED rows | FAILED stat card in snapshot | FAILED | One-liner from the queue entry's failure detail / last `operator_action`/`gate_fail` context; today the card shows only when it failed, never why |

**Anti-proposal:** `preflight_validated_at`, `queue_mode`, `display_ranks` arrive in the payload unrendered — leave them. Mode is visible via the mode toggle; ranks drive ordering; preflight age only matters on revalidate failure, which already flips state.

---

## Part C — Pipeline Screen Observability

Current rendering (verified): status pill (`PIPELINE_LIVE_PILL` + orchestrator-down override), `CurrentPhasePanel` (phase id, AgentBadge, reset counter, ElapsedTimer off `last_action_timestamp`/`sentinel_wait_started_at`/`waiting_for_human_at`, goal, 80-char `last_action`, per-role attempt table), `RoadmapPanel` (progress bar; expanded rows show duration, skill, cost incl. per-role, tokens, attempts, hold), "Run so far" strip (phases · attempts · duration · $ · tokens, 5s poll), hold-time chips, completion report markdown, `EscalationCommandPanel` (headline/advisory/resets/gated commands), activity feed (rich `humanizeSummary` for ~25 event types).

| # | Data point | Display location (component) | Trigger state | Display pattern |
|---|---|---|---|---|
| C1 | Running token/cost **including the live phase** | "Run so far" strip + `CurrentPhasePanel` | RUNNING / WAITING_FOR_SENTINEL | Strip gains "+ $0.31 · 41k tok this phase" from `phase_state.{role}_tokens_acc` (captured per attempt, currently unexposed — Phase 2). Honest caveat in tooltip: updates per attempt completion, not mid-stream |
| C2 | Agent liveness ("current action" proxy) | `CurrentPhasePanel`, beside AgentBadge | WAITING_FOR_SENTINEL | Pulse dot + "active 6s ago" from activity-stamp mtime age; turns amber as age approaches the stall threshold (threshold already known client-side from `poll_start` events). Closes the "is it doing anything?" gap without plugin changes |
| C3 | Last attempt outcome one-liner | `CurrentPhasePanel`, under last_action | Always during a phase; emphasized after a retry | `phase_state.last_attempt_summary` / `last_poll_reason` verbatim (already a dense prebuilt string — Section 6.4 — written for exactly this, read by no one) |
| C4 | Last meaningful agent output summary | `CurrentPhasePanel` (replaces/augments truncated `last_action` when fresher) | Agent just completed (gate evaluated) | Server-derived one-liner from the latest agent output artifact: planner → first `implementation_plan` item + test count; executor → "N files, tests passing/failing"; reviewer → verdict + blocking-issue count. All fields already validated by gates (executor_gate.py:298–315, reviewer_gate.py:519–568) |
| C5 | Error/warning counts by stage | `RoadmapPanel` phase rows (collapsed) | Phase has nonzero signals | Compact chips: "⚠2" (gate_warnings count), "↻3" (attempts>1), "⛔" (escalated) — needs `/api/metrics-summary` per-phase rows to stop dropping `gate_warnings`/`reachability_summary`/reset counters (Phase 2); expanded row gets the code list |
| C6 | Per-attempt cost in the feed | Activity feed `attempt_end` summary | Diagnostic events visible | "…succeeded in 312s · 18.4k tok · $0.06" once `attempt_end` carries token/cost deltas (Phase 5) |
| C7 | Structured escalation cause | `EscalationCommandPanel` collapsed disclosure (currently raw free-text `escalation_trigger_reason`) | WAITING_FOR_HUMAN | Badge from `trigger_class` + `last_error_code` ("stall · ERR_TESTS_FAILING · attempt 3/3") instead of prose parsing (Phase 1 capture) |

Constraint compliance: every proposal augments an existing element (strip, panel rows, chips, feed lines); none adds a panel; each is gated to the state where it answers a live question; completion summary untouched except C5 chips which collapse into the existing roadmap rows.

---

## Phased Roadmap

Ordering rule honored: schema/capture lands in the same phase or earlier than anything that surfaces it. Each phase is independently shippable and testable.

---

### Phase 1 — Telemetry foundations & integrity (capture only)

**Goal:** Every run, escalation, and human/automated intervention becomes analyzable from durable structured data alone — no log scraping, no prose parsing.
**Closes:** Q4 capture, Q5 capture, run-identity prerequisite for Q1/Q3; integrity findings 1, 2, 5, 7, and the Q2 outcome-coverage hole.

**Scope:**
- `orchestrator.py`: stamp a `run_id` (uuid, minted where `run_started_at` is set) into `pipeline_state`, every `_write_pipeline_event` line, `run_summary.json`, and the canonical metrics row.
- Structured escalation trigger: at the escalation chokepoints (main loop ~7224, repo-init ~5602, F4/F10 routes) set `escalation_trigger_class` enum — `{planner_retries_exhausted, executor_retries_exhausted, reviewer_routed, provider_rejected, stall, no_first_activity, infra_timeout, webhook_failure, resolver_failed, repo_init_failed, stamp_init_failed, reset_git_failed, gate_crash}` — persisted in `phase_state` (picked up by the metrics row) and added to `escalation_trigger` event detail alongside `last_error_code` and `last_poll_reason`. Keep the free-text reason as-is.
- Operator action events: server-side `operator_action` event (`{action, target, command?, source:"ui"}`) appended for: command, stop, resume-ready, resume-orchestrator, git-recover, queue add/delete/reorder/parent/clear/mode/relaunch/revalidate, launch, switch-project. **Writer decision:** single-line `O_APPEND` writes to `pipeline_events.jsonl` are atomic for <4KB lines on POSIX and match the orchestrator's append; alternatively a server-private `operator_events.jsonl` merged in `/api/events` — pick one, document it where `_write_pipeline_event` documents its contract.
- Consistency/integrity: add `retry_class` to planner `gate_fail` (5907); emit a `token_capture_warning` event (or `phase_state.token_capture_degraded` flag) on the `_sum_session_tokens` missing-file path; emit `run_segment_end` with segment totals when a project parks on escalation (closes the run_summary survivorship gap); heartbeat cron appends a `heartbeat_restart` event when it respawns the orchestrator.
- Events-file growth: adopt size-based rollover (e.g., rename at N MB; server tail already survives truncation, server.py:224) — decide and document.

**Acceptance criteria:** `jq` over `pipeline_events.jsonl` alone answers: failures by agent per run (group by `run_id`), interventions by type per week (`operator_action.action`), escalation triggers by class. New events render generically in the activity feed (verified tolerant path). Existing tests untouched; new pipeline tests for trigger-class assignment per chokepoint.

**Effort:** M. **Risks:** two-writer JSONL (mitigate via O_APPEND single-line or separate file — do not add locking); event schema is additive only (frontend already dual-keyed, finding 2 — codify `event`+`ts` as canonical before the server starts writing); the trigger-class enum must be assigned at *every* escalation route (F4/F10 paths included) or the taxonomy lies — grep-driven checklist in the PR.

---

### Phase 2 — Live run telemetry on the Pipeline screen (expose existing capture + render)

**Goal:** During a phase, the operator sees live cost/tokens, agent liveness, last attempt outcome, and per-phase warning counts without opening logs.
**Closes:** Part C targets C1–C5; Q3 surfacing; integrity findings 3, 6; Part B4's server dependency.

**Scope:**
- `ui/server.py` `GET /api/state`: from the phase_state it already reads (`_compute_escalation_view`), additionally expose `current_phase_tokens` (the three `{role}_tokens_acc` dicts + a summed `{total_tokens, cost_total}`), `last_poll_reason`, `last_attempt_summary`, `last_phase_outcome`; add `agent_activity_age_seconds` = now − mtime of `OPENCLAW_ROOT/workspace-{current_agent}/{current_agent}_activity.stamp` (null when absent).
- `GET /api/metrics-summary` per-phase rows: pass through `gate_warnings`, `reachability_summary`, `escalation_resets`, `nuclear_resets`, `reviewer_unverified_retries` (read-and-dropped today).
- `ui/index.html`: "Run so far" strip gains the live-phase suffix (C1); `CurrentPhasePanel` gains the liveness pulse (C2) and `last_attempt_summary` line (C3) and the agent-output one-liner (C4 — server derives it from the newest of the three output artifacts; add a small `latest_agent_output_summary` field to `/api/state` rather than shipping artifacts to the client); `RoadmapPanel` rows gain ⚠/↻/⛔ chips (C5).

**Acceptance criteria:** With a live run: token/cost figures visibly increase after each attempt; stamp-age indicator updates and goes amber on silence; after a retry, the panel shows the poller's verdict line; a phase that PASSed with demoted warnings shows "⚠N" collapsed and the codes expanded. Targeted tests: `/api/state` additive fields (absent-tolerant), stamp-age null path, metrics-summary passthrough.

**Effort:** M. **Risks:** writer/reader key coupling on `tokens_acc` (reuse exact `total_tokens`/`cost_total` names; add a server test that reads a fixture written by the orchestrator's writer function, not a hand-built dict — this is precisely the `_sum_session_tokens` bug class); stamp path must mirror the plugin's workspace layout (utils.ts `activityStampFilename`) — derive from config `openclaw_root`, not a literal.

---

### Phase 3 — Queue state clarity (one schema field + UI)

**Goal:** Every queue row answers "what is it waiting on, and for how long" at a glance, and needs-input is never visually conflated with failure.
**Closes:** Part B1, B2, B3, B7; integrity finding 8.

**Scope:**
- Schema: `state_changed_at` stamped on every entry state write, via a shared helper in `queue_semantics.py` (single source for both writers — orchestrator and server — mirroring the `scrub_parked_fields` pattern). Additive; legacy rows fall back to `added_at`.
- `ui/server.py`: expose `state_changed_at`; include a `failure_detail` one-liner on FAILED entries (spawn-failure reason already known at `_queue_run_trigger_next_logic` mark-FAILED time — persist it on the entry).
- `ui/index.html`: split `attention` bucket per B1 (`queueEntryBucket`, chips, pill colors); ELAPSED column covers READY/DEPENDENCY_HOLD/SKIPPED_PENDING from `state_changed_at` (B2); dependency line gains parent progress + held-duration (B3 — parent stats already in the payload); FAILED snapshot card shows `failure_detail` (B7).

**Acceptance criteria:** A DEPENDENCY_HOLD row shows "held 45m"; a READY row shows "queued 2h"; filter chips show separate Needs-input / Failed counts; a spawn-failed row's expansion states the reason. Tests: queue_semantics stamp helper (both writer paths), bucket mapping, legacy-row fallback.

**Effort:** S–M. **Risks:** the stamp must ride **existing** CAS mutation closures (side-effect-free rule, F9) — add it inside the mutation functions, never as a second write; two-writer field drift prevented by the shared helper (same reasoning as `PARKED_ENTRY_FIELDS`).

---

### Phase 4 — Historical analytics: PRD characteristics + estimates (capture + surface)

**Goal:** Run outcomes become correlatable with project/PRD shape, and queued projects show history-based cost forecasts.
**Closes:** Q2; Part B5, B6; integrity finding 4 (dark `metrics-global`).

**Scope:**
- `orchestrator.py`: capture `roadmap_stats` at run start — `{phases_total, subsystem_prefix_counts, behavioral_verification_blocks, roadmap_bytes}` (parse alongside the existing `phase_resolver` startup call) — persisted into `pipeline_state` and copied into `run_summary.json` (which already carries `outcome`, `idea_id` for the join).
- `ui/server.py`: `/api/queue` enrichment adds `estimated_cost`/`estimated_tokens` for READY/SKIPPED_PENDING rows lacking real totals: same-project per-phase medians from `metrics_history` (first read use of that file), cross-project fallback from run summaries; suppressed under a minimum-history floor. Extend `/api/metrics-global` per-project rows with `last_run_end`-relative fields the snapshot needs.
- `ui/index.html`: COST column "~$X est" styling (B5); snapshot prior-runs footer from `/api/metrics-global` (B6) — first UI consumer of the endpoint.

**Acceptance criteria:** New runs' `run_summary.json` contains `roadmap_stats`; a READY project with history shows a muted estimate; its expansion shows "N prior runs · last outcome"; `metrics-global` appears in the frontend network log. Offline: a one-liner jq join of `runs_index` × `run_summary.roadmap_stats` produces an outcome-vs-phase-count table (Q2 answerable).

**Effort:** M. **Risks:** estimate honesty (clearly marked, floor-gated — a bad estimate is worse than a dash); `metrics_history` becomes dual-read (orchestrator rebuild + server estimates) — read-only on the server side, no contention; run_summary remains additive (readers all use `.get`).

---

### Phase 5 — Per-attempt token/latency distribution (deep Q3 capture + feed surfacing)

**Goal:** Per-attempt cost and latency rows exist as first-class events, enabling distribution analysis (p50/p95 per agent, retry cost) and per-attempt feed display.
**Closes:** Q3 fully; Q1 refinement (cost of retries); Part C6; groundwork against integrity finding on plugin-discarded telemetry.

**Scope:**
- `orchestrator.py`: `_accumulate_role_tokens` returns the per-attempt **delta**; `attempt_end` detail gains `{tokens_delta, cost_delta}` (depends on Phase 1's `run_id` for cross-run grouping, and benefits from its `token_capture_warning` to mark zero-but-degraded attempts).
- `ui/index.html`: `attempt_end` humanize gains "· 18.4k tok · $0.06" (C6).
- Stretch (separately decidable): plugin `model_call_ended` appends per-call `{model, durationMs, usage}` lines to a sibling `{agent}_calls.jsonl` (never touching the empty-stamp contract); orchestrator archives it per phase. Gated on OpenClaw hook-payload verification; requires plugin rebuild + gateway restart discipline.
- Optional: webhook_client outcome events (`webhook_result` with return-token) for infra-flakiness rates (integrity finding 9).

**Acceptance criteria:** `jq` over events yields per-attempt `{agent, attempt, duration_s, tokens_delta, retry_class}` tuples grouped by `run_id`; feed lines show per-attempt cost; degraded token capture is visibly flagged rather than silently zero.

**Effort:** M (orchestrator+feed S–M; plugin stretch M–L). **Risks:** delta correctness when a session file is missing mid-phase (delta must clamp ≥0 and carry the degraded flag); plugin work has deploy-coupling (dist rebuild + gateway restart — historically a silent-failure zone) and should ship behind its own flag; event volume rises modestly — Phase 1's rotation policy must land first.

---

## Summary

| Phase | Theme | Depends on | Effort | Closes |
|-------|-------|------------|--------|--------|
| 1 | Telemetry foundations & integrity: run_id, structured escalation triggers, operator/intervention events, capture guards, rotation | — | M | Q4, Q5 capture; findings 1, 2, 5, 7; Q2 outcome coverage |
| 2 | Live pipeline telemetry: expose tokens_acc + outcome fields + stamp age; render live cost, liveness, attempt summary, warning chips | — (1 helpful for C7 badge) | M | Part C C1–C5; Q3 surfacing; findings 3, 6 |
| 3 | Queue state clarity: `state_changed_at`, needs-input vs failed split, hold durations, failure reasons | — | S–M | Part B1–B3, B7; finding 8 |
| 4 | Historical analytics: roadmap_stats in run_summary, history-based estimates, first metrics-global consumer | 1 (run coverage), 3 (queue UI touchpoints) | M | Q2; Part B5–B6; finding 4 |
| 5 | Per-attempt token/latency distribution: attempt deltas in events, feed cost lines, optional plugin/webhook telemetry | 1 (run_id, rotation, capture guard) | M | Q3 deep; Q1 refinement; Part C6; finding 9 groundwork |
