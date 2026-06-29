# Changelog

This project follows [Semantic Versioning](https://semver.org/). The first public release will be tagged `0.1.0`. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

> **Pre-1.0 development history.** Lullabeast was built across many TDD phases, each recorded in detail. The full, verbose engineering log — every fix, refactor, and decision, with internal phase tags (`P1 Stage *`, `T6.*`) and incident IDs — lives in **[`docs/archive/CHANGELOG-full.md`](docs/archive/CHANGELOG-full.md)**. That archive remains the system-of-record for the pre-release period. From `0.1.0` onward, this file carries concise, user-facing release notes.

## [Unreleased]

_No unreleased changes._

## [0.1.1] - 2026-06-29

A reliability pass on how the orchestrator stops in-flight agent sessions, plus activity-feed clean-ups. See **[`docs/archive/CHANGELOG-full.md`](docs/archive/CHANGELOG-full.md)** for the full engineering history.

### Added
- **Orchestrator-control rules for pipeline agents.** The planner, executor, and reviewer now carry a standing "Orchestrator Control" instruction set: end the turn the instant the output sentinel is written, and treat any `[ORCHESTRATOR CONTROL]` message as an authoritative stop signal. These rules are also re-injected after context compaction, so a long run can't silently drop them.

### Changed
- The stop message the orchestrator sends to interrupt an agent is now a short, authoritative directive, replacing wording that some models misread as adversarial ("untrusted") content and acted on instead of obeying.

### Fixed
- **Stalled or runaway agent sessions are now interrupted reliably.** The orchestrator decides whether an agent is still working by reading its session transcript rather than a short activity-timer heuristic — the old check could misread an agent that was mid–model-call as idle and skip the very interrupt it needed to perform. A still-streaming agent is now stopped; one that has genuinely finished is left alone (no gratuitous "stop" turn). All four interrupt paths (retry, stall/timeout, escalation, reviewer re-review) now share one mechanism.
- **Aborts no longer report a false "verify failed" in the activity feed.** After interrupting a session the orchestrator waits for it to actually go quiet instead of probing once immediately; the prior single-shot check tripped on the interrupt's own acknowledgement turn and flagged nearly every abort as failed. The warning now appears only for a genuine runaway that keeps streaming.
- **"Queue stalled" no longer floods the activity feed.** A halted queue records the stall once, when it happens, instead of re-emitting an identical event on every polling cycle (which could pile up dozens of duplicate rows over a long hold).
- **Abort outcomes are labelled correctly in the activity feed.** A skipped abort (the prior agent had already finished) now reads "already finished — no interrupt needed" instead of the misleading "Abort attempted", and an unconfirmed abort is shown as its own distinct state.
