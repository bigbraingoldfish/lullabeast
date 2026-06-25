# MultiLife Roadmap

- [x] `CORE-E1` | CRITICAL | Single-file scaffold and toroidal double-buffered grid model

  > Test: Open the single HTML file directly via `file://` with the network disabled; confirm it loads with zero network requests and that a unit harness can step a known small grid one generation and read back a deterministic next-state buffer.

  **Entry Criteria:** No prior code exists; the target is one self-contained HTML file at the project root (e.g., `index.html`) with all markup, CSS, and JS inline.

  **Exit Criteria:** `index.html` exists and opens from `file://` with no external requests; grid is a 120×80 `Uint8Array` (cell values `0`=dead, `1..4`=team) with toroidal 8-neighbor wrapping; a double-buffered `step()` reads a read-only snapshot and swaps buffers; pure grid/neighbor helpers are exported to a test harness; no `localStorage`/`sessionStorage`/cookies are used.

  **TDD Requirements:**
  - `grid.test.js`: Validates grid dimensions are 120×80, cell storage is a `Uint8Array`, and out-of-range coordinates wrap on both axes (toroidal neighbor indexing for the four edges and corners returns the wrapped cells).
  - `step-buffer.test.js`: Validates that `step()` computes the next buffer from a frozen snapshot (mutating the source mid-step does not affect results) and that buffers are swapped, proving order-independence.

  **Done Criteria:**
  - [ ] `index.html` opens from `file://` and issues zero network requests
  - [ ] Grid model is 120×80 backed by `Uint8Array` with toroidal Moore-neighbor wrapping
  - [ ] `step()` uses double-buffering (snapshot read, write to second buffer, swap)
  - [ ] No persistence APIs (`localStorage`, `sessionStorage`, cookies) are referenced
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can open the file directly in a browser with no internet and the page loads without errors.
  - **How we'll check:** Open `index.html` via `file://` with the network throttled to offline; confirm the page renders and DevTools Network tab shows no requests; run `grid.test.js` and `step-buffer.test.js` and confirm they pass.
  - **If this fails, the user sees:** The page fails to open offline, or it tries to reach the internet and stalls instead of running entirely on its own.

- [x] `CORE-E2` | CRITICAL | Shared survival and birth rules with QuadLife missing-color fallback

  > Test: Feed the rule engine hand-built neighborhoods and confirm survival (2–3 neighbors), birth at exactly 3 neighbors with plurality-team inheritance, and the QuadLife missing-color rule for a 1/1/1 three-distinct-team tie at teams=4.

  **Entry Criteria:** `CORE-E1` complete; grid model and double-buffered `step()` exist with exported helpers.

  **Exit Criteria:** Per-cell neighbor counting tallies `total` live neighbors and per-team counts; a live cell with `total` 2 or 3 survives, otherwise dies; a dead cell with `total` exactly 3 is born as the plurality team of its 3 parents; when teams=4 and the 3 parents are 3 distinct teams, the newborn takes the single missing 4th color; any other plurality tie resolves deterministically and stably.

  **TDD Requirements:**
  - `survival.test.js`: Validates a live cell survives with 2 or 3 live neighbors and dies with 0,1,4,5,6,7,8.
  - `birth.test.js`: Validates a dead cell is born only at exactly 3 live neighbors and inherits the plurality team.
  - `quadlife.test.js`: Validates that with teams=4 and 3 distinct-team parents, the newborn is the missing 4th color, and that other plurality ties resolve to the same team across repeated runs (determinism).

  **Done Criteria:**
  - [ ] Survival rule (2–3 neighbors) implemented correctly
  - [ ] Birth at exactly 3 neighbors with plurality-team inheritance
  - [ ] QuadLife missing-color rule for teams=4 1/1/1 ties
  - [ ] Deterministic, stable resolution for all other plurality ties
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** When the user watches the single-color (Classic) board, cells appear and disappear exactly as standard Conway's Game of Life does.
  - **How we'll check:** Seed a known still life (block) and a known oscillator (blinker) into the grid via the harness, step, and assert the block is unchanged and the blinker alternates; run `survival.test.js`, `birth.test.js`, and `quadlife.test.js`.
  - **If this fails, the user sees:** Patterns evolve incorrectly — stable shapes drift or vanish and familiar Conway patterns do not behave the way they should.

- [x] `CORE-E3` | CRITICAL | Conquest conversion rule, Classic/Conquest modes, and team count

  > Test: Place a surviving cell surrounded by a strict enemy majority and confirm it defects to that enemy; confirm a tie does not convert; confirm Classic mode forces teams=1 with no conversion and reproduces monochrome Conway.

  **Entry Criteria:** `CORE-E2` complete; shared survival/birth rules implemented.

  **Exit Criteria:** In Conquest mode (teams 2/3/4), a surviving cell defects to the single enemy team whose neighbor count strictly exceeds the cell's own team's neighbor count, choosing the most-numerous qualifying enemy; ties never convert; Classic mode forces teams=1 and applies only shared rules (no conversion); mode and team-count are runtime-selectable state that the step engine reads.

  **TDD Requirements:**
  - `conquest-convert.test.js`: Validates a surviving cell converts to a strictly-outnumbering enemy team, picks the most-numerous qualifying enemy, and does not convert on a tie or when its own team is not strictly outnumbered.
  - `classic-mode.test.js`: Validates that with mode=Classic, teams is forced to 1, no conversion occurs, and a blinker/block behave as standard Conway.

  **Done Criteria:**
  - [ ] Conquest conversion rule (strict enemy majority defection) implemented
  - [ ] Ties do not convert; most-numerous qualifying enemy is chosen
  - [ ] Classic mode forces teams=1 and disables conversion
  - [ ] Mode and team-count are runtime-readable simulation state
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** In Conquest mode the user sees colored teams contest the grid, with cells flipping allegiance along the boundaries between teams.
  - **How we'll check:** Seed a deterministic two-team front via the harness, step once, and assert that boundary cells outnumbered by the enemy now carry the enemy's team id; run `conquest-convert.test.js` and `classic-mode.test.js`.
  - **If this fails, the user sees:** Teams never actually fight — colors stay in their own regions and territory never flips, so Conquest looks identical to plain Conway.

- [x] `RENDER-E1` | HIGH | Canvas rendering with fresh-cell highlighting

  > Test: Render a grid containing a known mix of just-born/just-converted cells and surviving cells to the 480×320 canvas and confirm fresh cells draw at alpha 1.0 and surviving cells at alpha ≈0.72, with pixelated scaling and the correct background color.

  **Entry Criteria:** `CORE-E3` complete; step engine produces next-state buffers and exposes which cells are fresh (born or converted this generation).

  **Exit Criteria:** A canvas of internal resolution 480×320 renders the grid scaled responsively to container width with image smoothing disabled (pixelated); live-area background is `#060810`; team colors are fixed (1 Amber `#F5A623`, 2 Teal `#2DD4BF`, 3 Rose `#F43F5E`, 4 Violet `#A78BFA`); cells born or converted this generation render at alpha 1.0 and merely-surviving cells at alpha ≈0.72; the fresh highlight persists exactly one generation then reverts.

  **TDD Requirements:**
  - `fresh-flag.test.js`: Validates the step engine flags a cell as fresh on the generation it is born or converted and clears the flag on the next generation if it survives.
  - `render-alpha.test.js`: Validates the renderer maps fresh cells to alpha 1.0, surviving cells to alpha ≈0.72, dead cells to background, and uses the fixed per-team colors.

  **Done Criteria:**
  - [ ] Canvas internal resolution is 480×320 with pixelated (non-smoothed) scaling to container width
  - [ ] Live-area background is `#060810`; team colors match the fixed palette
  - [ ] Fresh cells render at alpha 1.0; surviving cells at alpha ≈0.72
  - [ ] Fresh highlight persists exactly one generation
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user sees the grid drawn in crisp team colors where newly born or just-flipped cells glow brighter than cells that merely held their ground, making active fronts pop.
  - **How we'll check:** Step a seeded grid one generation, capture the canvas pixels, and assert fresh cells are full-brightness while survivors are dimmer; confirm scaling is non-smoothed and the background is `#060810`; run `fresh-flag.test.js` and `render-alpha.test.js`.
  - **If this fails, the user sees:** The board looks flat — they cannot tell which cells just changed, or the colors/scaling are wrong (blurry or wrong background).

- [x] `UI-E1` | HIGH | Control surface: Run/Pause, Reset, Mode, Teams, Speed, spacebar, randomized reseed

  > Test: Exercise each control in the browser — verify Run/Pause toggles the loop, Spacebar mirrors only Run/Pause, Reset reseeds paused with stats cleared, Mode toggle reseeds and gates the Teams control, Teams toggle reseeds, and the Speed slider (1–60, default 18) changes generation rate.

  **Entry Criteria:** `RENDER-E1` complete; render loop and simulation state (mode, team count) exist.

  **Exit Criteria:** Exactly these controls exist (no step/paint/clear): Run/Pause (Spacebar mirrors Run/Pause only, never reseed-and-run), Reset (reseeds with current mode/team count, clears stats and history, leaves paused), Mode segmented `Conquest | Classic` (changing reseeds; Classic disables Teams and forces teams=1; Conquest restores Teams), Teams segmented `2 | 3 | 4` (active only in Conquest, changing reseeds), Speed slider 1–60 gens/sec default 18; reseed sets each cell live with probability ≈0.16 and a uniformly random active team; the run loop caps simulation sub-steps per animation frame (≤4) and uses typed arrays; interactive controls have visible keyboard focus styling.

  **TDD Requirements:**
  - `reseed.test.js`: Validates reseed sets ~16% of cells live (within tolerance over a large sample), assigns only active team ids uniformly, and clears stats/history.
  - `controls-state.test.js`: Validates Mode=Classic forces teams=1 and disables the Teams control, Mode=Conquest restores it, team-count/mode changes trigger reseed, and the speed value clamps to 1–60 with default 18.
  - `loop-substep.test.js`: Validates the frame loop executes at most 4 simulation sub-steps per frame and that the time accumulator resets when paused/stable.

  **Done Criteria:**
  - [ ] Only the specified controls exist (no step, paint, or clear)
  - [ ] Run/Pause toggle works; Spacebar mirrors Run/Pause only
  - [ ] Reset reseeds with current config, clears stats/history, stays paused
  - [ ] Mode/Teams changes reseed; Classic forces and locks teams=1
  - [ ] Speed slider spans 1–60 with default 18; reseed probability ≈0.16 with uniform random teams
  - [ ] Sub-steps per frame capped at ≤4; controls have visible focus styling
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can start and stop the simulation (with the button or Spacebar), reset it, switch between Conquest and Classic, pick 2/3/4 teams in Conquest, and change the speed.
  - **How we'll check:** In the browser, click Run/Pause and press Spacebar to confirm both toggle play state; click Reset and confirm the board reseeds paused; switch to Classic and confirm the Teams control disables and the board is single-color; move the Speed slider and confirm the update rate changes; run `reseed.test.js`, `controls-state.test.js`, `loop-substep.test.js`.
  - **If this fails, the user sees:** Buttons or the spacebar do nothing or do the wrong thing — for example the board won't start, switching mode doesn't reseed, or the speed slider has no effect.

- [x] `CORE-E4` | HIGH | Per-team statistics tracking (peak territory, generations survived)

  > Test: Run a deterministic seeded sequence and assert that for each team the recorded peak territory equals the maximum pixel count ever observed and that the last-alive generation equals the final generation the team had ≥1 cell.

  **Entry Criteria:** `UI-E1` complete; the run loop advances generations and reseed clears stats.

  **Exit Criteria:** Each generation the system records, per team, running peak territory (max pixel count ever) and the last generation at which the team had ≥1 cell; current per-team pixel counts and total live count are available to consumers; stats reset on reseed.

  **TDD Requirements:**
  - `stats-peak.test.js`: Validates peak territory is the running maximum per team and never decreases.
  - `stats-survival.test.js`: Validates the last-alive generation updates only while a team has ≥1 cell and freezes once the team is extinct.

  **Done Criteria:**
  - [ ] Per-team peak territory tracked as a running maximum
  - [ ] Per-team last-alive generation tracked correctly
  - [ ] Current per-team and total live counts exposed
  - [ ] Stats reset on reseed
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** Behind the scenes the system always knows each team's biggest-ever size and how long it lasted, which the readout and endgame card will display.
  - **How we'll check:** Drive a fixed seeded run through the harness, then assert recorded peak equals the observed maximum and last-alive generation equals the final generation with ≥1 cell for each team; run `stats-peak.test.js` and `stats-survival.test.js`.
  - **If this fails, the user sees:** The peak-territory and survival numbers shown later are wrong — a team's reported high-water mark or how long it lasted does not match what actually happened.

- [x] `ANALYTICS-E1` | HIGH | Live territory readout bars and generation counter

  > Test: With a known per-team distribution, confirm one readout cell per active team shows name, color swatch, current pixel count, a proportion bar sized to the team's share of live cells, and the `"<pct>% territory · peak <maxTerritory>"` caption, that a 0-cell team renders dimmed, and that the header shows the current generation.

  **Entry Criteria:** `CORE-E4` complete; per-team counts, totals, and peak are available each generation.

  **Exit Criteria:** One readout cell per active team renders: team name + color swatch, current pixel count, a horizontal proportion bar whose width equals the team's share of total live cells, and caption `"<pct>% territory · peak <maxTerritory>"`; a team at 0 cells renders visually dimmed; the header displays the current generation count; styling follows the tactical-console palette and monospace stack.

  **TDD Requirements:**
  - `readout-bar.test.js`: Validates proportion-bar width equals team share of total live cells and the caption string formats percentage and peak correctly.
  - `readout-dim.test.js`: Validates a team at 0 cells receives the dimmed visual state and the header reflects the current generation count.

  **Done Criteria:**
  - [ ] One readout cell per active team with name, swatch, count, proportion bar, caption
  - [ ] Caption format `"<pct>% territory · peak <maxTerritory>"`
  - [ ] 0-cell teams render dimmed
  - [ ] Header shows current generation count
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user sees a live per-team panel with each team's pixel count, share of the board, and peak, plus a running generation counter, and watches a wiped-out team dim to zero.
  - **How we'll check:** Set a known grid distribution, render, and assert each team's bar width matches its share, the caption text is exact, a zeroed team is dimmed, and the header shows the right generation; run `readout-bar.test.js` and `readout-dim.test.js`.
  - **If this fails, the user sees:** The team panel shows wrong or missing numbers, bars that don't match the board, or a generation counter that doesn't advance.

- [x] `ANALYTICS-E2` | HIGH | Territory-over-time line graph

  > Test: Feed a sequence of per-team counts through the ring buffer and confirm the graph draws one team-colored line per team, auto-scales to the running peak, shows the most recent ~940 generations (older samples dropped), and keeps an extinct team as a flat zero line.

  **Entry Criteria:** `ANALYTICS-E1` complete; per-team counts per generation are available.

  **Exit Criteria:** A line graph renders one team-colored line per team of pixel count over time, auto-scaled to the running peak, showing the most recent ~940 generations via a ring buffer that drops older samples; extinct teams remain visible as a flat zero line (never dropped, grayed, or hidden).

  **TDD Requirements:**
  - `graph-ring.test.js`: Validates the ring buffer retains ~940 samples and drops the oldest beyond that bound.
  - `graph-extinct.test.js`: Validates an extinct team's series continues as flat-zero samples and is still plotted (not removed) and that the vertical scale tracks the running peak.

  **Done Criteria:**
  - [ ] One team-colored line per team, auto-scaled to running peak
  - [ ] Most recent ~940 generations shown via ring buffer
  - [ ] Extinct teams rendered as flat-zero lines, never hidden
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user sees a live line graph of each team's territory over time and can watch a team's line collapse to zero and stay visible along the bottom after it is wiped out.
  - **How we'll check:** Push a known count sequence including an extinction, render, and assert each line's points match the data, the y-scale follows the peak, only ~940 samples are retained, and the extinct line stays flat at zero; run `graph-ring.test.js` and `graph-extinct.test.js`.
  - **If this fails, the user sees:** The graph is missing teams, drops a dead team's line, mis-scales, or stops updating as the battle progresses.

- [x] `CORE-E5` | CRITICAL | Stability detection via double-hash with extinction case and memory guard

  > Test: Run a known still life, a known oscillator, and an extinction scenario and confirm each is detected as stable, that "stabilized at generation X" reports the first appearance of the repeating state, that cycle length is computed correctly, and that the hash history clears and restarts once it exceeds ~20,000 stored states.

  **Entry Criteria:** `CORE-E4` complete; the step loop advances generations and exposes the full grid each generation.

  **Exit Criteria:** Each generation the full board is hashed by combining two independent rolling (FNV-style, different constants) passes into one composite key; a repeated key marks the board stable and records the generation where that state first appeared (cycle start); cycle length = current generation − cycle start; total extinction (zero live cells) is a distinct stable state; on detection the simulation auto-pauses; outcome is labeled extinction, still life (period 1), or oscillator (period N); the hash history is bounded — when stored states exceed ~20,000 it is cleared and detection restarts from the current generation; non-stabilizing runs continue indefinitely without error.

  **TDD Requirements:**
  - `hash-detect.test.js`: Validates a still life is detected as period 1, an oscillator (e.g., blinker) as period 2, and that the reported stabilization generation is the cycle start, not the detection generation.
  - `hash-extinction.test.js`: Validates a board reaching zero live cells is flagged as the distinct extinction stable state and auto-pauses.
  - `hash-memory-guard.test.js`: Validates the hash history clears and detection restarts once stored states exceed the ~20,000 bound, and that a chaotic run does not throw.

  **Done Criteria:**
  - [ ] Full-board double-hash composite key computed each generation
  - [ ] Repeat detection records cycle-start generation and computes cycle length
  - [ ] Extinction treated as a distinct stable state; auto-pause on detection
  - [ ] Outcome labeled extinction / still life (period 1) / oscillator (period N)
  - [ ] Hash history bounded at ~20,000 with clear-and-restart; non-stabilizing runs never error
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** When the board settles into a repeating pattern or dies out, the simulation stops on its own and is ready to report the result; chaotic runs just keep going.
  - **How we'll check:** Seed a blinker and confirm detection at period 2 with the stabilization generation equal to the cycle start; seed a dying pattern and confirm extinction is flagged; feed >20,000 distinct states and confirm history clears without error; run `hash-detect.test.js`, `hash-extinction.test.js`, `hash-memory-guard.test.js`.
  - **If this fails, the user sees:** The simulation never stops on a settled board, stops falsely on a still-changing board, or reports the wrong generation/period for when things stabilized.

- [x] `CORE-E6` | HIGH | Team ranking comparator

  > Test: Given team stats covering teams alive at the end, teams that died at different generations, and an all-extinct board, confirm the comparator orders by generations-survived desc, then final territory desc, then peak desc, so any end-alive team outranks any earlier-dead team and the last survivor leads an extinct board.

  **Entry Criteria:** `CORE-E5` complete; stabilization produces final per-team territory, last-alive generation, and peak.

  **Exit Criteria:** A pure comparator ranks teams by (1) generations survived descending, (2) final territory descending, (3) peak territory descending; any team alive at stabilization outranks any team that died earlier; among end-alive teams the largest territory wins; on an all-extinct board the last-surviving team ranks first.

  **TDD Requirements:**
  - `ranking.test.js`: Validates the three-key comparator ordering, that an end-alive team outranks an earlier-dead team regardless of peak, that final-territory breaks ties among equal-survival teams, and that peak breaks remaining ties.
  - `ranking-extinct.test.js`: Validates that on an all-extinct board the team with the latest last-alive generation ranks first.

  **Done Criteria:**
  - [ ] Comparator implements the exact three-key ordering
  - [ ] End-alive teams always outrank earlier-dead teams
  - [ ] Final territory and then peak break ties
  - [ ] All-extinct board ranks the last survivor first
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** When the game ends the user sees teams ranked in a sensible order — whoever lasted longest and held the most ground is on top.
  - **How we'll check:** Build fixture stat sets covering each tiebreak path plus an all-extinct board and assert the resulting order matches the spec; run `ranking.test.js` and `ranking-extinct.test.js`.
  - **If this fails, the user sees:** The endgame ranking is wrong — a team that died early is listed above a team still alive, or the wrong team is declared the winner.

- [x] `UI-E2` | HIGH | Endgame verdict card with summary, ranked table, and Run-again

  > Test: Trigger stabilization (still life, oscillator, and extinction) and confirm the overlay card shows the correct verdict label, the winner named/colored with the right phrasing, the summary line, a ranked table (rank, team, Lasted, Peak, Final), and a Run-again button that reseeds and immediately runs; confirm Spacebar does not reseed while the card is visible but the Run/Pause button triggers reseed-and-run when stable.

  **Entry Criteria:** `CORE-E5` and `CORE-E6` complete; stabilization detection and ranking comparator are available.

  **Exit Criteria:** On stabilization an overlay card displays: verdict label (extinction / still life / oscillator · period N); the winning team named and colored with `"<team> holds the board"` when it has surviving territory, else `"<team> lasted longest"`; a summary line with stabilization generation, cycle length (when >1), surviving-team count of total, and the winner's final territory percentage (when >0); a ranked table with columns rank, team, Lasted, Peak, Final using the `CORE-E6` order; a Run-again button that reseeds and immediately runs; when the card is visible Spacebar still toggles Run/Pause only (no reseed) while the Run/Pause button itself triggers reseed-and-run on a stable board; the card uses the tactical-console palette and monospace styling and the layout scales down to a narrow viewport.

  **TDD Requirements:**
  - `endgame-card.test.js`: Validates verdict labels for extinction/still life/oscillator, the two winner phrasings, the summary-line fields (including conditional cycle length and final-percentage), and the ranked-table columns/order.
  - `endgame-actions.test.js`: Validates Run-again reseeds and runs, the Run/Pause button triggers reseed-and-run when stable, and Spacebar toggles Run/Pause only (never reseed) while the card is shown.

  **Done Criteria:**
  - [ ] Verdict label correct for extinction, still life, and oscillator (period N)
  - [ ] Winner named/colored with correct "holds the board" vs "lasted longest" phrasing
  - [ ] Summary line includes stabilization generation, conditional cycle length, surviving count, and conditional winner final %
  - [ ] Ranked table shows rank, team, Lasted, Peak, Final in comparator order
  - [ ] Run-again reseeds and runs; stable Run/Pause button reseeds-and-runs; Spacebar toggles run/pause only
  - [ ] Card uses tactical-console palette/monospace and scales to narrow viewport
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** When the board stabilizes, the user sees a summary card telling them the outcome, who won, how long it took to stabilize, the cycle length, and a full ranked table, with a button to play again.
  - **How we'll check:** Force a blinker (oscillator period 2), a block (still life), and an extinction scenario; confirm each card's verdict, winner phrasing, summary fields, and table order are correct; click Run-again and confirm a fresh run starts; press Spacebar with the card up and confirm it does not reseed; run `endgame-card.test.js` and `endgame-actions.test.js`.
  - **If this fails, the user sees:** No endgame summary appears, or it reports the wrong winner, outcome, or numbers, or Run-again / the spacebar behave incorrectly.