# MultiLife

## Problem Statement

Conway's Game of Life is normally single-color: cells are alive or dead, and there is no notion of competing factions. Naïvely adding color via parent-inheritance (the classic Immigration / QuadLife variants) produces dynamics that are bit-for-bit identical to monochrome Life — the color is a cosmetic label and nothing actually competes. There is no readily available, self-contained tool that lets a user (a) watch genuinely *competitive* multi-team automata where territory is contested and flips, (b) compare that against plain single-color Conway, and (c) get quantitative endgame analytics (who won, how long the system took to stabilize, and how much territory each team ever held).

MultiLife solves this. The audience is a single technical user running it locally in a browser. The component's one job: simulate and visualize multi-team cellular automata and report a quantitative verdict when the pattern stabilizes. It must run fully offline with no external dependencies, no network calls, no fonts or scripts loaded from a CDN, and no persistence.

## Goals & Success Metrics

1. **Goal 1:** Faithfully simulate two distinct rule modes — single-color Conway ("Classic") and a competitive multi-team variant ("Conquest") — on a toroidal grid.
   - **Metric:** Next-state computation is deterministic and order-independent (computed from a snapshot of the current grid via double-buffering). In Classic mode, behavior is exactly standard Conway. In Conquest mode, contested cells visibly flip allegiance along fronts. Both modes selectable at runtime; switching modes reseeds.

2. **Goal 2:** On pattern stabilization, automatically halt and present a correct quantitative verdict.
   - **Metric:** Still lifes (period 1), oscillators (period ≥ 2), and total extinction are all detected and correctly labeled. The endgame card reports the generation at which the cycle began, the cycle length, the winner, and a per-team ranking (generations survived, peak territory, final territory) sorted per the rules in § Functional Requirements.

## User Stories

- **As a** user studying competitive automata, **I want** to watch 2–4 colored teams contest a grid where cells defect to stronger neighbors, **so that** I can observe territorial fronts forming, advancing, and collapsing.
- **As a** user, **I want** a live per-team readout of pixel count, percentage of territory, and peak territory, plus a territory-over-time line graph, **so that** I can track the battle as it unfolds and see a team get wiped out.
- **As a** user, **I want** an automatic endgame summary when the board stabilizes, **so that** I learn the winner, how many generations it took to stabilize, the cycle length, and the full ranking without watching every frame.
- **As a** user, **I want** a Classic single-color mode, **so that** I can study plain Conway stability with the same analytics.

## Functional Requirements

All requirements below are mandatory for v1.0.

### Platform & packaging

- The system **shall** be delivered as a **single HTML file** containing all markup, CSS, and JavaScript inline.
- The system **shall** run with **no external dependencies whatsoever** — no network requests, no external fonts, no CDN scripts, no build step. It must work opened directly from disk (`file://`) with no internet connection.
- The system **shall** use only vanilla JavaScript (ES2015+) and the Canvas 2D API. No frameworks.
- The system **shall not** use `localStorage`, `sessionStorage`, cookies, or any persistence; all state lives in memory for the session.

### Grid & simulation model

- The grid **shall** be **120 columns × 80 rows** of cells. Each cell holds a team id: `0` = dead, `1..4` = team.
- The grid topology **shall** be **toroidal** (edges wrap on both axes), so neighbor counting wraps around all four edges.
- Neighborhood **shall** be the 8 Moore neighbors.
- Next-state **shall** be computed from a read-only snapshot of the current grid into a separate buffer, then swapped (double-buffering), guaranteeing order-independence.
- On each generation the system **shall** track, per team: running peak territory (max pixel count ever reached) and the last generation at which the team had ≥ 1 cell.

### Rules — shared (both modes)

For each cell, count live neighbors (`total`) and tally live neighbors per team.

- **Survival:** a live cell with `total` of 2 or 3 survives; otherwise it dies (under/overpopulation).
- **Birth:** a dead cell with `total` exactly 3 becomes alive; its team is the **plurality** team among its 3 live parents.
- **QuadLife birth fallback:** when teams = 4 and the 3 parents are 3 distinct teams (a 1/1/1 tie), the newborn **shall** take the single *missing* (4th) color. (This preserves color symmetry; it is the standard QuadLife rule.) For any other plurality tie, deterministic resolution is acceptable as long as it is stable.

### Rules — Conquest mode (teams = 2, 3, or 4)

- In addition to the shared rules, a **surviving** cell **shall** defect ("convert") to an enemy team if a single enemy team's neighbor count *strictly* exceeds the cell's own team's neighbor count among its live neighbors. The cell becomes the most-numerous qualifying enemy team. Ties (no strict majority) do not convert.
- This conversion is the mechanism that produces battles. The build must accept that this is **not** pure Conway and that classic patterns (gliders, etc.) may not behave normally — this is expected, not a defect.

### Rules — Classic mode (single color)

- Classic mode **shall** force teams = 1 and apply only the shared survival/birth rules. With one team there is no conversion. This is exactly standard monochrome Conway, rendered in the team-1 color.

### Rendering

- The grid **shall** render to a canvas of internal resolution 480 × 320, scaled responsively to container width, with pixelated (non-smoothed) scaling.
- Background of the live area **shall** be `#060810`.
- A cell **born or converted in the current generation** ("fresh") **shall** render at full opacity (alpha 1.0); a cell that merely survived **shall** render dimmer (alpha ≈ 0.72). This makes active fronts visually pop. The "fresh" highlight persists for exactly the generation in which the cell was born or converted; if the cell survives into the next generation, it reverts to the dimmer alpha.

### Live readout (territory bars)

- The system **shall** render one readout cell per active team showing: team name + color swatch, current pixel count, a horizontal proportion bar whose width = that team's share of total live cells, and a caption line `"<pct>% territory · peak <maxTerritory>"`.
- A team at 0 cells **shall** render visually dimmed.
- The header **shall** display the current generation count.

### Territory-over-time graph

- The system **shall** render a line graph (one line per team, team-colored) of each team's pixel count over time, auto-scaled to the running peak, showing the most recent ~940 generations (older samples dropped from a ring buffer).
- Extinct teams **shall** remain visible as a flat line at zero; they are not dropped, grayed out, or hidden.

### Controls

The control set **shall** be limited to exactly the following (no step button, no paint/brush tools, no clear):

- **Run / Pause** — toggles the simulation. Spacebar **shall** mirror this toggle (it toggles Run/Pause only and does not trigger reseed-and-run when the endgame card is visible). When the board is stable, the button itself instead triggers a reseed-and-run.
- **Reset** — reseeds the grid with the current mode/team count, clears all stats and history, and leaves the simulation paused.
- **Mode** — segmented toggle: `Conquest | Classic`. Changing mode reseeds. Selecting Classic disables the Teams control and forces teams = 1; selecting Conquest restores the Teams control.
- **Teams** — segmented toggle `2 | 3 | 4`, active only in Conquest mode. Changing team count reseeds.
- **Speed** — slider, range 1–60 generations/second, default 18.

On reseed, each cell **shall** be independently set live with probability ≈ 0.16, assigned a uniformly random team from the active teams.

### Stability detection

- The system **shall** detect stabilization by **full-board state hashing**: each generation, hash the entire grid; if the hash has been seen before, the board has entered a repeating cycle and is stable.
- To make hash collisions negligible, the hash **shall** combine two independent rolling hashes (e.g., two FNV-style passes with different constants), keyed as a single composite string.
- Total extinction (zero live cells) **shall** be treated as a distinct stable state.
- On detection, the system **shall** auto-pause and display the endgame card.
- "Stabilized at generation X" **shall** report the generation where the now-repeating state *first appeared* (the start of the cycle), not the generation where the repeat was detected. Cycle length = current generation − that start generation.
- The system **shall** label the outcome as: extinction, still life (period 1), or oscillator (period N).

### Endgame card (verdict + ranking)

On stabilization the system **shall** display an overlay card containing:

- A verdict label (extinction / still life / oscillator · period N).
- The winning team, named and colored, with phrasing `"<team> holds the board"` if it has surviving territory, else `"<team> lasted longest"`.
- A summary line: stabilization generation, cycle length (if > 1), number of surviving teams of total, and the winner's final territory percentage (when > 0).
- A ranked table, one row per team, columns: **rank, team, Lasted (generations survived), Peak (max territory), Final (territory at stabilization)**.
- A **Run again** button that reseeds and immediately runs.

### Ranking logic (exact)

Teams **shall** be ranked by this comparator, in order:

1. Generations survived (last generation the team held ≥ 1 cell), **descending**.
2. Final territory at stabilization, **descending**.
3. Peak territory, **descending** (final tiebreak).

Consequence (required behavior): any team alive at stabilization outranks any team that died earlier; among teams alive at the end, the one holding the most territory wins; an all-extinct board ranks the last-surviving team first.

## Edge Cases

- If **all teams reach 0 live cells**, the system **shall** treat this as stable (extinction), rank by generations survived, and label the top team "lasted longest" rather than declaring a territory winner.
- If a birth has **3 distinct-team parents with teams = 4**, the system **shall** apply the QuadLife missing-color rule (see above).
- **Chaotic / very long-period runs:** If a run **never stabilizes** (chaotic or very long-period — common in Conquest on a torus), the system **shall** keep running indefinitely without error, and **shall** bound the hash-history memory (see Non-Functional / Reliability). The documented consequence is that cycles longer than the memory bound will not be detected; this is an accepted trade-off, not a bug.
- If the user **changes mode or team count mid-run**, the system **shall** reseed and reset all stats rather than attempting to carry state across an incompatible configuration.

## Non-Functional Requirements

- **Performance:** The simulation **shall** sustain its target speed (up to 60 generations/second on a 120×80 grid) without frame drops on a modern laptop. Use typed arrays (`Uint8Array`) for the grid and cap the number of simulation sub-steps executed per animation frame (e.g., ≤ 4) so a slow frame cannot spiral.
- **Reliability:** The state-hash history **shall** be memory-bounded — if it exceeds ~20,000 stored states, it is cleared and detection restarts from the current generation. This prevents unbounded memory growth on non-stabilizing runs.
- **Offline / Privacy:** The system **shall not** make any network request, load any external resource, or transmit any data. It **shall not** log or persist anything.
- **Accessibility:** Interactive controls **shall** have visible keyboard focus styling; Spacebar **shall** toggle run/pause.
- **Responsiveness:** The layout **shall** scale down cleanly to a narrow (mobile) viewport; the canvas scales to container width.
- **Aesthetic (binding, since it is a single deliverable):** dark "tactical console" identity — background `#0B0E14`, panels `#11151E`, hairline borders `#1A1F2B`/`#2A3243`, primary text `#C7CFDD`, dim text `#6B7689`. Monospace UI throughout via a system monospace stack (no web fonts). Team colors and names are fixed: team 1 Amber `#F5A623`, team 2 Teal `#2DD4BF`, team 3 Rose `#F43F5E`, team 4 Violet `#A78BFA`.

## Dependencies & Integrations

- **Upstream:** None. The component is self-contained.
- **Downstream:** None. No data is published anywhere.
- **External:** None. No external APIs, services, fonts, or scripts.

```json
{
  "integration_config": "N/A — fully self-contained offline artifact, no external integrations"
}
```

## Milestones & Timeline

Single-deliverable component; phases describe build order, not calendar dates.

1. **Phase 1: Core simulation**
   - Toroidal 120×80 grid, double-buffered step, typed arrays.
   - Shared survival/birth rules; Conquest conversion rule; QuadLife fallback.
   - Classic vs Conquest mode; Run/Pause, Reset, Mode, Teams, Speed controls.
   - Canvas render with fresh-cell highlighting.

2. **Phase 2: Analytics & endgame**
   - Live territory readout bars + generation counter.
   - Territory-over-time line graph.
   - State-hash stability detection (double hash, extinction case, memory guard).
   - Endgame overlay card with verdict, winner, summary, ranked table, Run-again.

## Risks & Mitigations

| Risk | Impact (Low/Med/High) | Mitigation Strategy |
|------|-----------------------|--------------------|
| Hash collision causes a false "stable" detection | Med | Combine two independent rolling hashes into a composite key; collision odds negligible over realistic run lengths. |
| Non-stabilizing run grows hash history without bound | Med | Clear and restart detection once stored states exceed ~20,000. |
| Slow frame causes simulation/render spiral at high speed | Med | Cap sub-steps per animation frame (≤ 4); reset the time accumulator when paused/stable. |
| Toroidal gliders create very long-period cycles undetected | Low | Accepted trade-off; documented in edge cases. Memory guard prevents resource issues. |
| Conquest mode rarely stabilizes, so endgame card seldom shown | Low | Documented expected behavior; Classic mode reliably stabilizes for users who want the verdict. |

## Open Questions

None. All questions resolved in session 2.

## Glossary & Domain Terms

- **Toroidal grid:** A grid whose edges wrap, so the top connects to the bottom and the left to the right — a doughnut topology with no boundaries.
- **Still life:** A pattern that does not change between generations (cycle period 1).
- **Oscillator:** A pattern that repeats with period ≥ 2.
- **Period / cycle length:** Number of generations after which a stable pattern repeats its exact state.
- **Immigration / QuadLife:** Standard 2-color / 4-color Conway variants where newborn cells inherit the plurality color of their parents; dynamics are identical to monochrome Life. The "Inheritance"-style baseline that Conquest mode deliberately departs from.
- **Conquest conversion:** This component's combat rule — a surviving cell defects to an enemy team that strictly outnumbers its own team among its neighbors.
- **Territory:** A team's live pixel count; "percentage of territory" is that count as a share of all live cells.
- **Fresh cell:** A cell born or converted in the current generation, rendered at full brightness. The fresh highlight persists for exactly one generation; on the next generation the cell reverts to the dimmer surviving-cell alpha if it remains alive.

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-06-17 | User (briefing) | Initial specification — all 12 sections populated. |
| 1.1 | 2026-06-17 | PRD Creator | Clarified: (a) deliberate visual alpha flash so territory flip/birth visibly pops, (b) Spacebar toggles Run/Pause only (does not reseed), (c) territory graph extinct teams render as flat-zero line (never hidden). |
| 1.2 | 2026-06-17 | User + PRD Creator | Added randomized reseed starting pattern: every cell is independently set live with probability ≈ 0.16 and assigned a uniformly random active team; added to Controls and Edge Cases sections. |
| 1.3 | 2026-06-17 | User + PRD Creator | Specified `2 | 3 | 4` team-count picker for Conquest mode; pinned to Controls + Rules (Conquest) sections; confirmed mid-run change reseeds and resets all stats. |
| 1.4 | 2026-06-17 | User + PRD Creator | Final pass on multi-team territory analytics, endgame card ranking logic, and stability detection hash-bounding details. Closed all Open Questions. PRD conversion-ready. |

> ✅ PRD CONVERSION-READY