# Lullabeast Pipeline Signals Plugin

OpenClaw plugin for Lullabeast pipeline liveness and output signaling.

## Purpose

This plugin registers:

- `agent_end` to write missing `{agent}_output.done` sentinels when a pipeline agent exits without writing one.
- `before_agent_finalize` to request an in-context revision when required output JSON fields are missing.
- `model_call_started`, `model_call_ended`, and `after_tool_call` to refresh `{agent}_activity.stamp`.
- A live agent-event subscription fallback so activity is still recorded when typed hook context is incomplete.

The orchestrator reads `{agent}_activity.stamp` during `poll_for_sentinel`; silence beyond `AUTODEV_STALL_TIMEOUT_*` is treated like a failed sentinel wait and follows the normal retry path.

## Install

From the repository root:

```bash
openclaw plugins install autodev/plugin --force
openclaw gateway restart
```

`install.sh` performs the plugin install, sets `plugins.entries.autodev-pipeline-signals.hooks.allowConversationAccess=true`, and validates typed hook registration.

## Validate

Check registration:

```bash
openclaw plugins inspect autodev-pipeline-signals --json
```

Expected:

- `plugin.status` is `"loaded"`.
- `plugin.hookCount` is `5`.
- `typedHooks` includes `agent_end`, `before_agent_finalize`, `model_call_started`, `model_call_ended`, and `after_tool_call`.

Check runtime behavior during a live pipeline run:

```bash
stat <project>/.autodev/pipeline/executor_activity.stamp
stat ~/.openclaw/agents/executor/sessions/<session-id>.jsonl
```

The stamp mtime should advance with the matching session JSONL mtime while the agent is active.

## Development

```bash
npm test
npm run typecheck
npm audit
```
