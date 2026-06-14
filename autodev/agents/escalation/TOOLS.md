# TOOLS.md — Escalation Agent

## Available Tools

- **File read** — Read workspace files, pipeline state JSONs, agent output JSONs, project source code, logs, and any file needed for diagnosis. You have broad read access across the system.
- **Shell (read-only)** — Run read-only diagnostic commands (`ls`, `find`, `cat`) to inspect file existence and content.
- **message** — Send outbound notifications on the configured external channel (e.g. Signal, Discord) through OpenClaw. You **must** follow [Message tool and peer resolution](#message-tool-and-peer-resolution) on every call. Do not guess addresses.
- **File write (sandboxed)** — Write access is restricted to your workspace directory by OpenClaw's sandbox. The `pipeline-project/` symlink inside your workspace is your only write path to shared pipeline files. You write exactly **one** pipeline file:
  - `pipeline-project/.autodev/pipeline/escalation_summary.json` — the dashboard advisory (see the escalation-summary skill), written BEFORE you notify. You do **not** write `escalation_output.json` / `escalation_output.done` or any command file — the operator answers from the dashboard or by replying on the configured channel, and the Lullabeast server writes the command.

## Message tool and peer resolution

Pipeline-driven escalations use **session keys** like `agent:escalation:pipeline:…` — not the same key as a live DM thread. The `message` tool does **not** auto-reply to the operator unless the platform binds a default; you must supply a valid **peer** for the active channel.

1. **Read local OpenClaw config** on the host: `~/.openclaw/openclaw.json` (or the path your deployer documents). Use `channels.signal` (or the relevant channel block), **bindings** (which agent matches which peer), and **allowFrom** / group policy as the source of truth for who is allowed and how traffic is routed. Do not invent phone numbers, group names, or IDs.
2. **Signal direct (E.164):** use the operator's real E.164 in international form, consistent with your deployment. Never use obvious placeholders or "example" numbers in real sends.
3. **Signal groups:** use the **base64** group id from `bindings` or your signal-cli / gateway listing — not a human label, not numeric ids from other chat apps.
4. **Optional local file:** if your install documents `OPERATOR_PEER.local` (from `OPERATOR_PEER.local.example`), read the **resolved** file on disk — never commit real values to git.

## Pipeline session vs live session

- **Pipeline session:** created for hooks like `pipeline:phase-0:…:escalation` — you still resolve the human recipient from config as above; assuming "reply in thread" without checking session metadata often fails.
- **Diagnostic smoke:** see `SMOKE_PROMPT.md` in this workspace for gateway-only connectivity checks (no pipeline output files).

## When delivery fails (RPC / tool errors)

If the tool returns errors such as `Signal RPC -1` or "Failed to send message", treat that as a **failed outbound delivery** (wrong peer, policy, session, or upstream gateway error). Report the **verbatim** error text to the operator when appropriate. Do **not** conclude that "Signal is disconnected" or "unconfigured" unless you have evidence (e.g. channel disabled in `openclaw.json` or health check). A working inbound chat does not guarantee a given `to` field is valid.

## Path Convention

- ✅ CORRECT: `pipeline-project/.autodev/pipeline/escalation_summary.json`
- ❌ WRONG: `~/.openclaw/pipeline-project/.autodev/pipeline/escalation_summary.json` — writes to absolute paths outside your workspace are silently accepted by the write tool but the files are discarded by OpenClaw's sandbox. The file will appear to succeed but will not be created.
- ❌ WRONG: any other absolute path outside the workspace — same sandbox discard behavior

## Explicitly Denied by OpenClaw Policy

- `edit` — cannot apply patches or targeted edits to files
- `apply_patch` — not available
- `exec` — cannot run pipeline scripts, orchestrator commands, or agent triggers
- `process` — cannot manage services or restart processes
- `browser` — not available

You can read everything. You can write only `escalation_summary.json` (the dashboard advisory) through the workspace symlink.
