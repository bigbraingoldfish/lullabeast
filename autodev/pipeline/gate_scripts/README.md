# Gate scripts — the pipeline's deterministic decision layer

This directory holds the **gate scripts**: small, deterministic Python programs the orchestrator
([`../orchestrator.py`](../orchestrator.py)) invokes — almost always as **subprocesses** — to decide what
happens next in the pipeline. They are the seam between the orchestrator's control flow and the
filesystem state the LLM agents produce.

> **Canonical spec:** `CLAUDE.md` → **"Gate Script Interface Contract"** is the source of truth, and
> `autodev/docs/PIPELINE-SPEC.md` (§4.5 advisory channels, §13 repo-init) has the full detail. This README is
> an **orientation**, not a second spec — if it ever disagrees with CLAUDE.md, CLAUDE.md wins.

## Why subprocesses (not imports)?

Gates run in their own process so they are **isolated and deterministic**: a gate gets a clean interpreter,
can't leak state into the orchestrator, and — critically — a gate that *crashes* (uncaught traceback,
timeout) is observed by the runner as a **safe failure** rather than taking down the pipeline loop. The
orchestrator wraps every gate subprocess with `GATE_SUBPROCESS_TIMEOUT` and treats a crash/timeout as the
conservative outcome (planner/executor → `False`; reviewer → `ROUTE_ESCALATE`; resolver/init → handled as
error). All gate-script paths are built from the single `GATE_SCRIPTS_DIR` constant in `orchestrator.py`.

## Two signalling conventions

There is **no single universal exit-code contract** — there are two, and which one applies depends on the
gate:

### 1. Verdict gates — `planner_gate.py`, `executor_gate.py`, `reviewer_gate.py`
- **Always exit 0.** The verdict is a **string on stdout**, read by the orchestrator via `result.stdout`.
- planner / executor → `PASS` / `FAIL`. reviewer → `PASS` or a **route token** (`ROUTE_EXECUTOR`,
  `ROUTE_PLANNER`, `ROUTE_ESCALATE`, `*_UNVERIFIED`, `MISSING_ARTIFACTS`, `CONTRACT_FAILURE`).
- A **non-zero exit means the gate script itself crashed** (a traceback) — the runner does not parse a
  crashed gate's stdout; it fails safe.
- Failure **detail never rides stdout** — it flows on side channels (see below).

### 2. Resolver / init gates — `phase_resolver.py`, `repo_init_check.py`
- Signal via **exit code**:

  | Exit | `phase_resolver.py` | `repo_init_check.py` |
  |------|---------------------|----------------------|
  | `0`  | `PENDING` / `PIPELINE_COMPLETE` on stdout | all preconditions pass |
  | `1`  | error (roadmap missing / non-absolute path / write failure) | a failed precondition |
  | `2`  | next phase is blocked (`[!]`) | — |

## Side channels (verdict-gate detail)

Verdict gates keep the verdict terse on stdout and write detail to files under the project's
`.autodev/pipeline/`:

- `executor_gate_detail.json` — the executor-gate **FAIL** detail channel (consumed by `write_failure_context`).
- `gate_warnings.json` — **demoted, non-blocking** interpretive warnings the gate PASSes on and the *reviewer*
  later adjudicates (preserved, not removed, on the PASS path).
- `executor_advisory_detail.json` — the executor **PASS-path advisory** channel (e.g. reachability); drained
  and then removed by the orchestrator.
- `last_error_code` in `phase_state.json` — the machine-readable error code (written via `utils.py`).

## The determinism requirement

Gates **must be deterministic**: they receive filesystem state and return a stable verdict. **No LLM calls,
no network, no clock-dependence, no random output.** The orchestrator calls them at fixed points and relies on
a repeatable result. (The advisory `reachability/` resolvers are static analysers — still deterministic.)

## One intentional exception: the planner gate is invoked two ways

`planner_gate.py` is the only gate evaluated by **two** mechanisms, on purpose:

- `Orchestrator.run_planner_output_gate` — the **normal-loop** gate, via **subprocess** (process isolation).
- `Orchestrator.planner_output_is_valid` — a **restart-detection** helper, via **in-process `import`** so a
  test's workspace patches take effect (a subprocess would inherit the real `OPENCLAW_ROOT` and ignore the
  mocks).

Both call `planner_gate.evaluate_planner` and share **one verdict contract**. They are deliberately *not*
reconciled to a single mechanism — the two callers have genuinely different needs (isolation vs.
test-mockability). See the comments at both call sites in `orchestrator.py`.

## `utils.py` is shared infrastructure, not a gate

`utils.py` holds the workspace path constants, the atomic phase-state writers, `load_json_safe`, and
`path_escapes_workspace` (the realpath workspace-boundary check used by the executor and reviewer gates). It is
imported by the gates; it is never invoked as one.
