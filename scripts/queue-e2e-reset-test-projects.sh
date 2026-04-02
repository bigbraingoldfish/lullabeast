#!/usr/bin/env bash
# Reset queue-test1..5 for strict queue E2E: pipeline artifact cleanup, git main + commit,
# default single-phase roadmap (variant A). Optional: --create-phaseonly for V4 warn path.
#
# Usage (from autodev-ui repo root, or any cwd):
#   ./scripts/queue-e2e-reset-test-projects.sh
#   ./scripts/queue-e2e-reset-test-projects.sh --create-phaseonly
#   PROJECTS_ROOT=/other/projects ./scripts/queue-e2e-reset-test-projects.sh
#
# Environment:
#   AUTODEV_ROOT   default ~/.openclaw
#   PROJECTS_ROOT  default /home/pi/projects

set -euo pipefail

AUTODEV_ROOT="${AUTODEV_ROOT:-$HOME/.openclaw}"
PROJECTS_ROOT="${PROJECTS_ROOT:-/home/pi/projects}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CREATE_PHASEONLY=0

for arg in "$@"; do
  case "$arg" in
    --create-phaseonly) CREATE_PHASEONLY=1 ;;
    -h|--help)
      echo "Usage: $0 [--create-phaseonly]"
      exit 0
      ;;
  esac
done

ts="$(date +%Y%m%d%H%M)"
queue_json="${AUTODEV_ROOT}/pipeline_queue.json"
state_json="${AUTODEV_ROOT}/pipeline_state.json"

echo "[INFO] AUTODEV_ROOT=$AUTODEV_ROOT"
echo "[INFO] PROJECTS_ROOT=$PROJECTS_ROOT"

if [[ -f "$queue_json" ]]; then
  cp -a "$queue_json" "${queue_json}.bak.${ts}"
  echo "[INFO] Backed up pipeline_queue.json -> .bak.${ts}"
fi
if [[ -f "$state_json" ]]; then
  cp -a "$state_json" "${state_json}.bak.${ts}"
  echo "[INFO] Backed up pipeline_state.json -> .bak.${ts}"
fi

clean_artifacts() {
  local d="$1"
  [[ -d "$d" ]] || return 0
  # shellcheck disable=SC2164
  cd "$d"
  rm -f \
    current_phase.json \
    planner_output.json planner_output.done \
    executor_output.json executor_output.done \
    reviewer_output.json reviewer_output.done \
    phase_state.json \
    failure_context.json \
    executor_gate_detail.json \
    pending_escalation_command.json \
    escalation_output.json escalation_output.done \
    2>/dev/null || true
  rm -f ./*.done 2>/dev/null || true
  find . -maxdepth 1 -name 'phase_state_*' -type f -delete 2>/dev/null || true
}

write_roadmap_a() {
  local title="$1"
  local out="$2"
  cat >"$out" <<EOF
# ${title}
- [ ] \`CORE-E1\` | LOW | Minimal E2E phase
  > Test: pytest passes
  **Entry Criteria:** Empty
  **Exit Criteria:** One trivial change
  **TDD Requirements:** - optional
  **Done Criteria:** - [ ] tests pass
EOF
}

write_roadmap_v1_sidecar() {
  local out="$1"
  cat >"$out" <<'EOF'
# V1 startup-complete (copy to roadmap.md on queue-test1 only): see 00-source-of-truth.md
- [x] `CORE-E1` | LOW | Completed for resolver PIPELINE_COMPLETE
  > Test: n/a
  **Entry Criteria:** n/a
  **Exit Criteria:** n/a
  **TDD Requirements:** - n/a
  **Done Criteria:** - [x] done
EOF
}

reset_runner_repo() {
  local name="$1"
  local title="$2"
  local dir="${PROJECTS_ROOT}/${name}"
  mkdir -p "$dir"
  clean_artifacts "$dir"
  cd "$dir"

  if git rev-parse --git-dir >/dev/null 2>&1; then
    while IFS= read -r b; do
      [[ -z "$b" ]] && continue
      git branch -D "$b" 2>/dev/null || true
    done < <(git branch --list 'phase/*' | sed 's/^[* ]*//')
    if git show-ref --verify --quiet refs/heads/main; then
      git checkout main
    elif git show-ref --verify --quiet refs/heads/master; then
      git checkout master
      git branch -M main
    else
      git checkout -b main 2>/dev/null || { git branch -M main 2>/dev/null || true; git checkout -b main; }
    fi
    git reset --hard HEAD 2>/dev/null || true
  else
    rm -rf .git
    git init
    git branch -M main
  fi

  write_roadmap_a "$title" "${dir}/roadmap.md"
  write_roadmap_v1_sidecar "${dir}/roadmap.B.v1-complete.md"
  echo "# ${name}" > "${dir}/README.md"
  git add -A
  if git diff --cached --quiet 2>/dev/null; then
    echo "[OK] $dir (no file changes to commit)"
  else
    git commit -m "queue-e2e: reset roadmap A + V1 sidecar" || true
  fi
  if ! git rev-parse HEAD >/dev/null 2>&1; then
    echo "[ERROR] $dir has no commit after reset" >&2
    exit 1
  fi
  echo "[OK] $dir branch=$(git branch --show-current) commit=$(git rev-parse --short HEAD)"
}

reset_runner_repo "queue-test1" "Queue Test 1"
reset_runner_repo "queue-test2" "Queue Test 2"
reset_runner_repo "queue-test3" "Queue Test 3"
reset_runner_repo "queue-test4-child" "Queue Test 4 Child"
reset_runner_repo "queue-test5-esc" "Queue Test 5 Escalation"

if [[ "$CREATE_PHASEONLY" -eq 1 ]]; then
  PO="${PROJECTS_ROOT}/queue-test-phaseonly"
  rm -rf "$PO"
  mkdir -p "$PO"
  cd "$PO"
  if git init -b phase/demo 2>/dev/null; then
    :
  else
    git init
    git checkout -b phase/demo
  fi
  write_roadmap_a "Queue Test Phase Only" "${PO}/roadmap.md"
  echo "# phase only (no main/master)" > "${PO}/README.md"
  git add -A
  git commit -m "init on phase/demo only"
  echo "[OK] $PO branches: $(git branch -a | tr '\n' ' ')"
  if git branch --list main master | grep -q .; then
    echo "[WARN] main/master unexpectedly present; adjust git version or delete branches manually for V4 warn path"
  fi
fi

echo ""
echo "=== Next steps ==="
echo "1) Empty queue in UI or overwrite ${queue_json} with {\"queue\":[],\"queue_mode\":\"manual\",\"last_updated\":\"\"} if appropriate."
echo "2) V1 (startup PIPELINE_COMPLETE): on queue-test1 run: cp roadmap.B.v1-complete.md roadmap.md && git add roadmap.md && git commit -m 'V1 complete roadmap'"
echo "3) Symlink before launch: ln -sfn <active_repo> ${AUTODEV_ROOT}/pipeline-project"
echo "4) Run strict E2E per plans/Active/queue-e2e-manual-validation/05-STRICT-E2E-RUNBOOK.md"
