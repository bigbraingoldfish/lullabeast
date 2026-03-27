---
name: cli-tooling-reviewer
description: Domain guidance for reviewing CLI and tooling phases. Loaded when phase category is CLI.
---

# CLI/Tooling Review Guidance

## Exit codes and errors
Test success and all error conditions return expected exit codes. Confirm no hidden successes (wrapper returning 0 on failure/signal).

## Help text accuracy
Run `tool --help` and each subcommand help. Verify: lists every flag with correct descriptions, shows defaults, required args clearly indicated.

## Stream separation
Capture stdout and stderr separately. Normal output only on stdout; errors only on stderr. Meaningful stderr on failure (no silent failures).

## Subprocess testing
All features tested via shell invocation (subprocess), not just internal functions. Assert return codes + stdout + stderr content.

## Shell safety
Feed arguments with spaces and shell metacharacters. Verify tool still works.

## Non-interactive behavior
Simulate non-interactive env (pipe input, unset TTY). Verify CLI doesn't hang. Ctrl+C returns conventional interrupt code (130).

## Attribution
- Plan: missing CLI contract, unspecified exit codes, ambiguous output format.
- Impl: behavior differs from planned contract, missing error handling, incorrect stream usage.
