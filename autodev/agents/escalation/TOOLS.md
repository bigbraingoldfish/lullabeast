# TOOLS.md — Escalation Agent

## Available Tools

- **File read** — Read workspace files, pipeline state JSONs, agent output JSONs, project source code, logs, and any file needed for diagnosis. You have broad read access across the system.
- **Shell (read-only)** — Run diagnostic commands only:
  - `curl http://<traffic-cop-host>:9000/health` — check traffic cop / local model availability
  - `ps aux | grep llama` — check if llama-server is running
  - `ls`, `find`, `cat` — inspect file existence and content
- **File write (sandboxed)** — Write access is restricted to your workspace directory by OpenClaw's sandbox. The `pipeline-project/` symlink inside your workspace is your only write path to shared pipeline files. You write exactly two files:
  - `pipeline-project/escalation_output.json` — the resume command from the operator
  - `pipeline-project/escalation_output.done` — empty sentinel, written after the JSON

## Path Convention

- ✅ CORRECT: `pipeline-project/escalation_output.json`
- ❌ WRONG: `~/.openclaw/pipeline-project/escalation_output.json` — writes to absolute paths outside your workspace are silently accepted by the write tool but the files are discarded by OpenClaw's sandbox. The file will appear to succeed but will not be created.
- ❌ WRONG: `/home/pi/.openclaw/pipeline-project/escalation_output.json` — same sandbox discard behavior

## Explicitly Denied by OpenClaw Policy

- `edit` — cannot apply patches or targeted edits to files
- `apply_patch` — not available
- `exec` — cannot run pipeline scripts, orchestrator commands, or agent triggers
- `process` — cannot manage services or restart processes
- `browser` — not available

You can read everything. You can write only your two output files through the workspace symlink.
