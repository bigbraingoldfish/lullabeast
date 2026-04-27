#!/usr/bin/env bash
# Save pipeline_queue.json, pipeline_state.json, and pipeline-project symlink target for strict dual-test restore.
# Usage: ./scripts/queue-e2e-strict-freeze.sh
# Restore: cp pipeline_queue.json.strict-freeze.TS ~/.openclaw/pipeline_queue.json (and state); ln -sfn "$(cat ...readlink)" ...

set -euo pipefail
OPENCLAW_ROOT="${OPENCLAW_ROOT:-$HOME/.openclaw}"
TS="$(date +%Y%m%d-%H%M%S)"
for f in pipeline_queue.json pipeline_state.json; do
  if [[ -f "${OPENCLAW_ROOT}/${f}" ]]; then
    cp -a "${OPENCLAW_ROOT}/${f}" "${OPENCLAW_ROOT}/${f}.strict-freeze.${TS}"
    echo "[ok] ${f}.strict-freeze.${TS}"
  else
    echo "[skip] missing ${OPENCLAW_ROOT}/${f}"
  fi
done
PP="${OPENCLAW_ROOT}/pipeline-project"
if [[ -L "$PP" ]] || [[ -e "$PP" ]]; then
  python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$PP" > "${OPENCLAW_ROOT}/pipeline-project.strict-freeze.${TS}.readlink" || true
  echo "[ok] pipeline-project.strict-freeze.${TS}.readlink"
else
  echo "[skip] missing $PP"
fi
echo "STRICT_FREEZE_TS=${TS}"
