---
name: cli-tooling-planner
description: Domain guidance for planning CLI and tooling phases. Loaded when phase category is CLI.
---

# CLI/Tooling Planning Guidance

## Define the full CLI contract
For every command/subcommand: argument names, types, defaults, required/optional, help descriptions. Include expected output format (text vs JSON) and example usage.

## Exit code strategy
Define which exit codes indicate success vs error types (0=success, 1=general error, 2=invalid usage). This drives testing.

## Pass criteria
- `tool --help` shows usage without errors.
- Valid input produces expected output on stdout.
- Invalid input returns nonzero exit code and error on stderr.
- Specify expected stdout vs stderr separation.

## Plan quiet/verbose modes
If CLI may run under agents or scripts, plan `--quiet`/`--verbose` flags and document their effect on output.

## Scope logically
Break into sub-tasks by subcommand group. Implement and test each separately.
