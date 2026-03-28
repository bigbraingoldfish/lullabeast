# AutoDev — Post-MVP Enhancement Ideas

*Captured after initial MVP scoping. These are held for after first user feedback round.*

---

## Documentation Phase Intelligence

**PRD-to-Roadmap Alignment Check** *(in progress — optional, pre-setup)*
An agent that reads both the finished PRD and the generated roadmap and produces a gap report: requirements in the PRD with no corresponding roadmap phase, and roadmap phases with no clear PRD backing. Surfaces misalignment before a line of code is written. Fits naturally as an optional step between Generate Roadmap and Continue to Setup.

**Adversarial Phase Reviewer** *(optional, pre-setup)*
An agent that reads the roadmap adversarially — constructing specific failure hypotheses per phase rather than validating the plan. For each phase: a confidence score and a concrete failure scenario (context limits, gate rejection reasons, scope issues). Forces specificity by requiring the agent to justify its pessimism. Needs calibration testing before exposing to users — output quality is assumed, not proven.

**Targeted PRD Assumption Research**
Rather than broad market research on the PRD, an agent that identifies the three to five most load-bearing unvalidated assumptions in the PRD and researches specifically those. More focused than general deep research and produces actionable signal rather than generic best practices.

---

## Pipeline Transparency

**Phase Plan Visibility in Monitor**
Surface a condensed view of the current phase's planner output in the Pipeline Monitor — what was planned, what's being executed, what the reviewer is checking. Makes the pipeline feel less like a black box during active runs.

**Post-Run Summary Screen**
After pipeline completion: a summary of what was built, which phases succeeded cleanly, which required retries or resets, what was skipped. Over multiple projects this becomes a portfolio view.

---

## Escalation Experience

**Escalation Agent Chat in Monitor**
Allow the operator to ask follow-up questions to the escalation agent directly in the Pipeline Monitor before committing to a resume command. The escalation agent message display (MVP) is the foundation — this extends it to a two-way interaction. Requires meaningful UI refactor of the monitor screen. Longer-term direction.

---

## Power User Features

**Skill Library Browser in UI**
A UI for reading, editing, and eventually creating SKILL.md files without touching the filesystem. Makes the discipline skill system visible and tunable to users who want to customize pipeline behavior for their specific stack.

---

## Distribution and Discovery

**Demo Mode**
A read-only replay of a recorded pipeline run — monitor, activity feed, escalation events — requiring zero setup. Zero-friction way to show the product to people who don't have OpenClaw installed. High value for public release.

**Notification Reliability**
A simpler, more reliable escalation notification path than Signal. When the pipeline needs human input overnight, the operator should know without having to check the browser. Webhook to a configurable endpoint or email fallback.

---

## Long-Term / Significant Scope

**Post-Build Validation Loop**
After a project is built: run it, test it, compare the running implementation against the original PRD, surface misalignments. Requires significant new tooling, agent structure, and runtime access. Right product direction if this ever becomes a commercial product. Not in scope for current AutoDev architecture.

---

*Last updated: March 2026. Revisit after first tester feedback round.*
