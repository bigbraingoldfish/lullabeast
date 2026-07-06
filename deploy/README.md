# Lullabeast container deploy

**Status: in progress.** This directory accumulates the Deploy Simplification
release artifacts. As of DS-2b it contains the golden OpenClaw config; the
Dockerfile, entrypoint, compose file, quickstart, and the OpenClaw licensing
note land with DS-3.

Current contents:

- [openclaw.template.json](openclaw.template.json): the canonical OpenClaw config
  for owned-OpenClaw installs. The DS-3 container entrypoint renders it into
  `/data/openclaw/openclaw.json` on first boot, substituting the
  `${HOOKS_TOKEN}` / `${GATEWAY_TOKEN}` secrets (generated at first boot) and the
  `PLANNER_MODEL` / `EXECUTOR_MODEL` / `REVIEWER_MODEL` / `PRD_MODEL` env knobs.
  Helpers live in `autodev/installer/openclaw_template.py`; the doctor's
  `template_conformance` check (owned mode only) flags any drift between a live
  config and this template.
- [CONFIG-AUDIT.md](CONFIG-AUDIT.md): the key-by-key decision record behind the
  template: what ships, what changed, what stays operator-only, and the
  minimum-hardware statement.

## Cost tracking and the $0 case

The template ships `models.pricing.enabled: true` and complete pricing blocks for
its 4 recommended OpenRouter models, so runs on the shipped defaults report real
dollar costs in the dashboard. Every other provider or model is
meter-it-yourself: **if a run's cost shows $0, OpenClaw has no pricing for the
model that ran**, not that the run was free. To add pricing for your own model,
follow the walkthrough in [SETUP.md](../SETUP.md) under "Cost metrics:
configuring OpenClaw so Lullabeast can report run cost".

One-line spend warning: agent pipelines are token-hungry. Cache reads dominate
and bill at a fraction of fresh input, but bills are real; watch the Monitor's
cost strip on your first runs.
