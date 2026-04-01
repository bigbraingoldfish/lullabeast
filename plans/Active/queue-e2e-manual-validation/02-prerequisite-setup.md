# PREREQUISITE SETUP — Complete before functional validation (Part 3)

**Behavioral spec:** This file only creates **test repos** and an empty queue. Intended queue/escalation behavior for validation is defined in **`00-source-of-truth.md`** (primary: **Escalation Park-and-Advance** plan, not raw TASK-03 alone).

**Purpose:** Clean slate on the queue, **new unique paths**, **minimal single-phase** roadmaps so agents finish fast. No deep functional validation here beyond “files exist” and “server answers `/api/state`”.

---

## A. Clean slate

1. Open AutoDev UI (e.g. `http://localhost:18790` or your configured host/port).
2. Go to **Project Queue**. Remove **every** entry (**Remove** on each row) until the queue is empty.
3. If older Pi test dirs from a previous round still exist (e.g. `queue-test-a` / `queue-test-b` / `queue-test-c`), **do not reuse** them for this run—use only the new paths in section **C** below.

---

## B. Server sanity

- Confirm `GET /api/state` returns **200** (browser or `curl`).
- Note current `queue_mode` after cleanup; set **auto** vs **manual** only when **Part 3 — Functional validation** instructs you.

---

## C. Create minimal test projects (unique names — single phase each)

Base path: `/home/pi/projects` (adjust if your Pi uses another root).

Each repo: `mkdir -p <path>/.git`, one `roadmap.md`, **one** phase `CORE-E1` only—minimal text for fastest pipeline progress.

### Project 1 — `queue-e2e-solo-alpha` (no dependency)

```bash
mkdir -p /home/pi/projects/queue-e2e-solo-alpha/.git
cat > /home/pi/projects/queue-e2e-solo-alpha/roadmap.md << 'EOF'
# Solo Alpha
- [ ] `CORE-E1` | LOW | Return constant
  > Test: pytest passes
  **Entry Criteria:** Empty
  **Exit Criteria:** One function exists
  **TDD Requirements:** - `test_app.py`
  **Done Criteria:** - [ ] tests pass
EOF
```

### Project 2 — `queue-e2e-solo-beta` (no dependency)

```bash
mkdir -p /home/pi/projects/queue-e2e-solo-beta/.git
cat > /home/pi/projects/queue-e2e-solo-beta/roadmap.md << 'EOF'
# Solo Beta
- [ ] `CORE-E1` | LOW | Return constant
  > Test: pytest passes
  **Entry Criteria:** Empty
  **Exit Criteria:** One function exists
  **TDD Requirements:** - `test_app.py`
  **Done Criteria:** - [ ] tests pass
EOF
```

### Project 3 — `queue-e2e-solo-gamma` (no dependency)

```bash
mkdir -p /home/pi/projects/queue-e2e-solo-gamma/.git
cat > /home/pi/projects/queue-e2e-solo-gamma/roadmap.md << 'EOF'
# Solo Gamma
- [ ] `CORE-E1` | LOW | Return constant
  > Test: pytest passes
  **Entry Criteria:** Empty
  **Exit Criteria:** One function exists
  **TDD Requirements:** - `test_app.py`
  **Done Criteria:** - [ ] tests pass
EOF
```

### Project 4 — `queue-e2e-parent` (dependency parent)

```bash
mkdir -p /home/pi/projects/queue-e2e-parent/.git
cat > /home/pi/projects/queue-e2e-parent/roadmap.md << 'EOF'
# Parent Lib
- [ ] `CORE-E1` | LOW | Provide helper
  > Test: pytest passes
  **Entry Criteria:** Empty
  **Exit Criteria:** helper exists
  **TDD Requirements:** - `test_parent.py`
  **Done Criteria:** - [ ] tests pass
EOF
```

### Project 5 — `queue-e2e-child-of-parent` (depends on parent — set parent in UI after add)

```bash
mkdir -p /home/pi/projects/queue-e2e-child-of-parent/.git
cat > /home/pi/projects/queue-e2e-child-of-parent/roadmap.md << 'EOF'
# Child Consumer
- [ ] `CORE-E1` | LOW | Use helper
  > Test: pytest passes
  **Entry Criteria:** Empty
  **Exit Criteria:** uses parent
  **TDD Requirements:** - `test_child.py`
  **Done Criteria:** - [ ] tests pass
EOF
```

### Project 6 — `queue-e2e-extra-slot` (extra independent for rotation / halt experiments)

```bash
mkdir -p /home/pi/projects/queue-e2e-extra-slot/.git
cat > /home/pi/projects/queue-e2e-extra-slot/roadmap.md << 'EOF'
# Extra Slot
- [ ] `CORE-E1` | LOW | Return constant
  > Test: pytest passes
  **Entry Criteria:** Empty
  **Exit Criteria:** One function exists
  **TDD Requirements:** - `test_app.py`
  **Done Criteria:** - [ ] tests pass
EOF
```

---

## D. Add to queue (order for Part 3)

- Add projects via UI in the **order Part 3 specifies** (e.g. alpha → beta → gamma for linear auto; parent then child, then **Set parent** for dependency scenarios).
- Do **not** start long pipeline runs until **Part 3** begins.

---

## E. Handoff

When **A–D** are done, read **`00-source-of-truth.md`**, then open **`03-functional-validation-prompt.md`** and follow it step-by-step. Record queue JSON snapshots or screenshots when Part 3 asks.
