#!/usr/bin/env bash
# Save pipeline_queue.json, pipeline_state.json, and pipeline-project symlink target for strict dual-test restore.
# Usage: ./scripts/queue-e2e-strict-freeze.sh
# Restore: cp pipeline_queue.json.strict-freeze.TS ~/.openclaw/pipeline_queue.json (and state); ln -sfn "$(cat ...readlink)" ...

set -euo pipefail
AUTODEV_ROOT="${AUTODEV_ROOT:-$HOME/.openclaw}"
TS="$(date +%Y%m%d-%H%M%S)"
for f in pipeline_queue.json pipeline_state.json; do
  if [[ -f "${AUTODEV_ROOT}/${f}" ]]; then
    cp -a "${AUTODEV_ROOT}/${f}" "${AUTODEV_ROOT}/${f}.strict-freeze.${TS}"
    echo "[ok] ${f}.strict-freeze.${TS}"
  else
    echo "[skip] missing ${AUTODEV_ROOT}/${f}"
  fi
done
PP="${AUTODEV_ROOT}/pipeline-project"
if [[ -L "$PP" ]] || [[ -e "$PP" ]]; then
  readlink -f "$PP" > "${AUTODEV_ROOT}/pipeline-project.strict-freeze.${TS}.readlink" || true
  echo "[ok] pipeline-project.strict-freeze.${TS}.readlink"
else
  echo "[skip] missing $PP"
fi
echo "STRICT_FREEZE_TS=${TS}"
