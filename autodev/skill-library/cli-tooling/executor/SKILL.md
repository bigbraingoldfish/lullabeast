---
name: cli-tooling-executor
description: Domain guidance for implementing CLI and tooling phases. Loaded when phase category is CLI.
---

# CLI/Tooling Implementation Guidance

## Argument parsing
Use the language's standard CLI library (argparse/click for Python, cobra for Go). Define all arguments with types, defaults, and help text in the parser, not manually.

## Subcommands
Use subparsers/subcommands rather than manual flag checks. Prevents argument/command confusion.

## Exit code discipline
Always exit 0 for success, >0 for errors. Use specific codes for different error types. Ensure wrappers propagate signal exit codes correctly.

## Stream separation
- stdout: normal results only.
- stderr: errors, warnings, debug info.
- No verbose/debug output on stdout unless explicitly requested (--verbose).

## Non-interactive support
Check stdin.isatty() before prompting. Provide --no-prompt/--yes for non-interactive use. If no TTY and interactive required, exit with informative error.

## Input safety
Sanitize/escape user inputs for shell safety. Use shlex.quote or equivalent when building commands. Handle paths with spaces.

## Testing
Test via subprocess invocation, not just function calls. Assert exit codes, stdout content, stderr content for: valid input, missing input, invalid input.
