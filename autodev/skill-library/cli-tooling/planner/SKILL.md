---
name: cli-tooling-planner
description: Domain guidance for planning CLI and tooling phases. Loaded when phase category is CLI.
---

# CLI/Tooling Planning Guidance

## Decomposition checklist
- Top-level command and each subcommand as a separate sub-area.
- Argument parsing, command dispatch, output formatting, and exit-code policy as distinct concerns.
- `--quiet` / `--verbose` / output format flags (text vs JSON) planned upfront when the tool may run under agents or scripts.

## Interfaces & contracts to specify
For every command/subcommand: argument names, types, defaults, required vs optional, help description, and an example invocation. Pin the output format on stdout (text or JSON schema) and what goes on stderr.

## Edge cases — must enumerate, not generalise
Missing required argument, unknown flag, mutually exclusive flags both passed, empty stdin when stdin is read, oversized input, non-UTF8 input, broken pipe on stdout, interrupt (SIGINT) mid-run.

## Pass criteria patterns
- `tool --help` exits 0 and shows the documented usage.
- Valid invocation produces the documented stdout shape (text-snapshot or JSON-schema assertion).
- Each enumerated error case returns the documented non-zero exit code and writes the error to stderr (not stdout).
- stdout/stderr separation is asserted explicitly — not assumed.

## Anti-patterns to avoid
- Exit codes not specified ("nonzero on error" is not a spec) — pin per outcome: 0 success, 1 general error, 2 invalid usage, plus any domain-specific codes.
- Error messages written to stdout, breaking pipe consumers.
- `--help` output that diverges from the actual flag set.
- Tool that prints colour codes when stdout is not a TTY.

## TDD test structure
Minimum: one help-output snapshot per command, one happy-path stdout assertion per command, one exit-code + stderr assertion per enumerated error case.
