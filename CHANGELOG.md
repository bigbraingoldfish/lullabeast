# Changelog

This project follows [Semantic Versioning](https://semver.org/). The first public release will be tagged `0.1.0`. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

> **Pre-1.0 development history.** Lullabeast was built across many TDD phases, each recorded in detail. The full, verbose engineering log — every fix, refactor, and decision, with internal phase tags (`P1 Stage *`, `T6.*`) and incident IDs — lives in **[`docs/archive/CHANGELOG-full.md`](docs/archive/CHANGELOG-full.md)**. That archive remains the system-of-record for the pre-release period. From `0.1.0` onward, this file carries concise, user-facing release notes.

## [Unreleased]

_No unreleased changes._

## [0.2.0] - 2026-06-29

Adds a deterministic in-turn tool-loop catcher and tightens roadmap verification, on top of a reliability pass on how the orchestrator stops in-flight agent sessions and recovers from reviewer failures. See **[`docs/archive/CHANGELOG-full.md`](docs/archive/CHANGELOG-full.md)** for the full engineering history.

### Added
- **Deterministic tool-loop catcher.** If an agent gets stuck re-running the same tool call with identical input inside one turn — a loop OpenClaw's own guard misses (it keys on identical input *and* output, defeated by jittered command output) and stall detection misses (the agent keeps its activity stamp fresh, so it looks busy) — the orchestrator now detects it within ~15–30 s, stops the looping session, and retries it fresh, escalating with a clear "tool loop" cause only if it keeps looping. Per-role sensitivity knobs (`TOOL_LOOP_REPEAT_LIMIT_{PLANNER,EXECUTOR,REVIEWER}`; executor defaults higher for headroom; set `0` to disable a role). Surfaces as a `tool_loop_detected` activity-feed event. Previously such a loop ran until the multi-hour infrastructure backstop.
- **Roadmap generator requires machine-checkable verification.** The roadmap-converter's "How we'll check" guidance now bans subjective, human-eyeball criteria ("visually confirm…") and requires a headless, programmatic pass/fail (e.g. a DOM / computed-style assertion for visual phases), so a generated check can't trap the reviewer in an unverifiable loop.
- **Orchestrator-control rules for pipeline agents.** The planner, executor, and reviewer now carry a standing "Orchestrator Control" instruction set: end the turn the instant the output sentinel is written, and treat any `[ORCHESTRATOR CONTROL]` message as an authoritative stop signal. These rules are also re-injected after context compaction, so a long run can't silently drop them.

### Changed
- The stop message the orchestrator sends to interrupt an agent is now a short, authoritative directive, replacing wording that some models misread as adversarial ("untrusted") content and acted on instead of obeying.

### Fixed
- `install.sh` now deploys all four roadmap-converter skills — the `format-correction` skill was previously omitted from the deploy loop and only reached the runtime via a manual copy.
- **"Reset Reviewer" now grants a full fresh retry budget.** After the reviewer's automatic retries were exhausted, clicking **Reset Reviewer** from the escalation panel previously gave the reviewer only a single attempt before it escalated again — the internal contract-failure and verification-retry counters survived the reset, so they were already at their limit (the counter was observed climbing 3 → 4 → 5 across three resets). Reset Reviewer now clears those counters, the same way **Reset Execution** already does for the executor, so each reset restores the reviewer's complete retry budget.
- **Reviewer infrastructure failures are reported with their real cause.** When the reviewer's model server returns an error mid-review — for example a 500 from GPU contention or model eviction — rather than the reviewer genuinely failing to produce a verdict, the escalation now names that as the cause and carries a distinct error code, instead of the misleading "reviewer ended without a verdict (gave up or was cut off)." In this case the implementation is often correct, so the message points you at the model host rather than the code. Automatic fresh-session retries are unchanged.
- **Stalled or runaway agent sessions are now interrupted reliably.** The orchestrator decides whether an agent is still working by reading its session transcript rather than a short activity-timer heuristic — the old check could misread an agent that was mid–model-call as idle and skip the very interrupt it needed to perform. A still-streaming agent is now stopped; one that has genuinely finished is left alone (no gratuitous "stop" turn). All four interrupt paths (retry, stall/timeout, escalation, reviewer re-review) now share one mechanism.
- **Aborts no longer report a false "verify failed" in the activity feed.** After interrupting a session the orchestrator waits for it to actually go quiet instead of probing once immediately; the prior single-shot check tripped on the interrupt's own acknowledgement turn and flagged nearly every abort as failed. The warning now appears only for a genuine runaway that keeps streaming.
- **"Queue stalled" no longer floods the activity feed.** A halted queue records the stall once, when it happens, instead of re-emitting an identical event on every polling cycle (which could pile up dozens of duplicate rows over a long hold).
- **Abort outcomes are labelled correctly in the activity feed.** A skipped abort (the prior agent had already finished) now reads "already finished — no interrupt needed" instead of the misleading "Abort attempted", and an unconfirmed abort is shown as its own distinct state.
