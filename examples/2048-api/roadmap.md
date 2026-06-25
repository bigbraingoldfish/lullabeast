# 2048 Game Roadmap

- [x] `CORE-E1` | CRITICAL | Scaffold the single-file HTML project with a blank 4×4 CSS grid, inline CSS, and inline JavaScript structure (branch: `phase/core-e1`)

  > Test: Open `index.html` in a browser and verify a visible 4×4 grid container is rendered with 16 empty cells.

  **Entry Criteria:** N/A
  **Exit Criteria:** `index.html` exists in project root with inlined `<style>` and `<script>` tags, a `<div id="board">` containing 16 cell `<div>` elements arranged in a CSS Grid (4 columns), and a visible board with minimum dimensions 300×300 px.
  **TDD Requirements:**
  - `tests/core-e1.test.js`: Verifies that the DOM contains exactly 16 `.cell` elements inside `#board`
  - `tests/core-e1.test.js`: Verifies that `#board` has `display: grid` and `grid-template-columns: repeat(4, 1fr)`
  **Done Criteria:**
  - [ ] `index.html` renders a visible 4×4 grid with empty cells
  - [ ] Inline CSS and JS are present; no external dependencies
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player opens the game in a browser and sees a blank 4×4 playing board ready for tiles.
  - **How we'll check:** Open `index.html` in a headless browser, query `#board`, assert it has 16 child `.cell` elements and total area ≥ 300×300 px.
  - **If this fails, the user sees:** The page is blank or has a broken layout instead of a square grid.

- [x] `CORE-E2` | CRITICAL | Implement the grid data model and tile spawning logic with the 90/10 value distribution (branch: `phase/core-e2`)

  > Test: Reload `index.html` and verify exactly two tiles appear in random cells; repeatedly reload and confirm values are either 2 or 4 with 4 being rare (~10%).

  **Entry Criteria:** `index.html` from CORE-E1 exists with a renderable 4×4 grid.
  **Exit Criteria:** A JavaScript `Grid` class (or equivalent) maintains a 4×4 numeric array; method `spawnTile()` places a value of 2 (90% probability) or 4 (10% probability) in a random empty cell; game initialization spawns exactly two tiles.
  **TDD Requirements:**
  - `tests/core-e2.test.js`: Verifies `spawnTile()` on an empty 4×4 grid places exactly one tile in a previously empty cell
  - `tests/core-e2.test.js`: Verifies over 1000 spawns the ratio of 4s is between 5% and 15%
  - `tests/core-e2.test.js`: Verifies `Grid` initializes with exactly two non-zero cells
  **Done Criteria:**
  - [ ] A `Grid` model exists with a 4×4 numeric state array
  - [ ] `spawnTile()` respects the 90/10 value distribution
  - [ ] Page load renders two tiles in random empty cells
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player opens the game and sees two numbered tiles (usually 2, occasionally 4) placed in random spots on the board.
  - **How we'll check:** Open `index.html`, count `.tile` elements in the DOM, assert count is 2; inspect their text content and assert values are in {2, 4}; reload 10 times and confirm 4 appears at least once.
  - **If this fails, the user sees:** The board starts empty, has more or fewer than two tiles, or shows incorrect tile values.

- [x] `CORE-E3` | CRITICAL | Implement four-directional movement and merge logic including compress-merge-recompress order and no-double-merge rule (branch: `phase/core-e3`)

  > Test: Using a seeded/debug board state `[2,2,2,0, ...]`, press ArrowLeft and verify the board becomes `[4,2,0,0, ...]`, not `[8,0,0,0, ...]`; test ArrowRight on `[2,2,2,2]` and verify `[0,0,4,4]`.

  **Entry Criteria:** `Grid` model and tile spawning from CORE-E2 are functional.
  **Exit Criteria:** Methods exist to move the board in all four directions; compression slides tiles to the far edge; merges happen from the far side first; each tile merges at most once per move; the board state updates correctly for edge cases `[2,2,2]`, `[2,2,2,2]`, `[2,2,4,4]` in all directions.
  **TDD Requirements:**
  - `tests/core-e3.test.js`: Verifies `[2,2,2,0]` moved left becomes `[4,2,0,0]`
  - `tests/core-e3.test.js`: Verifies `[2,2,2,2]` moved left becomes `[4,4,0,0]`
  - `tests/core-e3.test.js`: Verifies `[2,2,4,4]` moved left becomes `[4,8,0,0]`
  - `tests/core-e3.test.js`: Verifies `[2,2,4]` moved left becomes `[4,4,0]` (no double-merge to 8)
  - `tests/core-e3.test.js`: Verifies identical input state produces identical output for all four directions on a suite of 10 pre-defined board matrices
  **Done Criteria:**
  - [ ] All four directional moves are implemented
  - [ ] Merge order is far-side-first and no tile merges twice in one move
  - [ ] Edge-case board configurations produce correct results
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player presses an arrow key and all tiles slide and merge in that direction exactly like the classic 2048 game.
  - **How we'll check:** Programmatically inject specific board states into the `Grid` model, simulate `ArrowLeft`, `ArrowRight`, `ArrowUp`, `ArrowDown`, and assert the resulting array matches expected outputs for each edge case.
  - **If this fails, the user sees:** Tiles merge in the wrong order, chain-merges happen in a single move, or tiles do not slide all the way to the edge.

- [x] `CORE-E4` | CRITICAL | Implement scoring, valid-move detection, and post-move tile spawning (branch: `phase/core-e4`)

  > Test: Perform a move that causes a merge, confirm the score increases by the merged tile’s value; perform a move that changes nothing and confirm no new tile spawns and the score does not change.

  **Entry Criteria:** Directional move logic from CORE-E3 is complete.
  **Exit Criteria:** Score state tracks the sum of all values of tiles created by merging; a move that does not change the board state is considered invalid and does not trigger `spawnTile()`; valid moves trigger exactly one `spawnTile()` call after state updates.
  **TDD Requirements:**
  - `tests/core-e4.test.js`: Verifies merging two `4` tiles adds `8` to the score
  - `tests/core-e4.test.js`: Verifies an invalid move (e.g., pressing ArrowUp on a board where no tiles can move up) does not change the board or score and does not spawn a tile
  - `tests/core-e4.test.js`: Verifies a valid non-merge move (tile slides but does not merge) spawns exactly one new tile and does not change the score
  **Done Criteria:**
  - [ ] Score accurately reflects merged tile values
  - [ ] Invalid moves are rejected and do not spawn tiles
  - [ ] Valid moves spawn exactly one tile after board update
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player sees their score go up when tiles merge, and a new tile appears after every meaningful move, but nothing happens when they press a key that would not change the board.
  - **How we'll check:** Load the game, force a board state with a known merge, trigger a move, assert score element text increases by the expected value; force a blocked board state, trigger a move, assert tile count and score remain unchanged.
  - **If this fails, the user sees:** The score does not increase on merges, new tiles appear even on useless key presses, or the game freezes after some moves.

- [x] `CORE-E5` | HIGH | Implement win detection, loss detection, and `localStorage` best-score persistence with graceful degradation (branch: `phase/core-e5`)

  > Test: Programmatically set a tile to 2048 and verify a win condition is flagged; fill the board with no adjacent equal tiles and verify a loss condition is flagged; reload the page after scoring and verify the best score persists.

  **Entry Criteria:** Scoring and move validation from CORE-E4 are functional.
  **Exit Criteria:** Win is detected when any tile value ≥ 2048; loss is detected when no empty cells remain and no adjacent equal tiles exist in any direction; best score is saved to and loaded from `localStorage` on every score change and page load; all `localStorage` access is wrapped in `try/catch` and silently degrades to session-only if unavailable.
  **TDD Requirements:**
  - `tests/core-e5.test.js`: Verifies a board containing a 2048 tile triggers the win flag
  - `tests/core-e5.test.js`: Verifies a completely full board with no mergeable neighbors triggers the loss flag
  - `tests/core-e5.test.js`: Verifies best score is written to `localStorage` after the score exceeds the previous best
  - `tests/core-e5.test.js`: Verifies `localStorage` exceptions are caught and the game continues without throwing
  **Done Criteria:**
  - [ ] Win and loss detection work for the defined conditions
  - [ ] Best score persists across page reloads
  - [ ] `localStorage` failures do not break the game
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player sees a win message when they create a 2048 tile, a game over message when no moves are left, and their best score remains visible even after closing and reopening the browser.
  - **How we'll check:** Set board state to include a 2048 tile via the JS console or test harness, assert win state is true; set a full unmergeable board, assert loss state is true; set a score, reload the page, assert best-score display reads the saved value; block `localStorage` and confirm the game still runs.
  - **If this fails, the user sees:** The game never declares a win or loss, or the high score resets every time the page refreshes, or the game crashes when private-browsing mode disables storage.

- [x] `UI-E1` | HIGH | Render tiles dynamically from the grid model, display current and best scores, wire keyboard input, and add the New Game button (branch: `phase/ui-e1`)

  > Test: Load the game, press ArrowLeft, and verify the DOM tiles match the model’s updated positions; verify the score display updates; click the New Game button and verify the board resets to two tiles and the current score resets to 0 while the best score stays unchanged.

  **Entry Criteria:** Complete game logic (CORE-E2 through CORE-E5) exists and is testable in isolation.
  **Exit Criteria:** DOM tile elements are created/updated/removed to reflect the `Grid` state after every valid move; current score and best score are rendered in dedicated elements; ArrowUp/ArrowDown/ArrowLeft/ArrowRight call the movement methods; the New Game button reinitializes the grid model, score, and board but preserves the best score in `localStorage`; default browser scrolling on arrow keys is prevented.
  **TDD Requirements:**
  - `tests/ui-e1.test.js`: Verifies pressing ArrowRight on a board with slideable tiles updates the DOM to reflect the new positions within one animation frame
  - `tests/ui-e1.test.js`: Verifies clicking a `.new-game` button resets the current score to 0, clears all tiles, and respawns exactly two tiles
  - `tests/ui-e1.test.js`: Verifies pressing ArrowDown does not trigger a page scroll (default is prevented)
  **Done Criteria:**
  - [ ] Tiles render correctly from the model after every move
  - [ ] Score and best score are visible and update in real time
  - [ ] Keyboard controls work and do not scroll the page
  - [ ] New Game button resets board and current score
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player sees numbered tiles appear, move, and update on the board as they use arrow keys; their score is visible at the top; they can click a button to start over at any time.
  - **How we'll check:** Load `index.html`, assert two tiles are in the DOM; dispatch `ArrowLeft` keyboard event, wait one frame, assert tile positions/text in the DOM match the internal model; click the New Game button, assert the DOM shows exactly two new tiles and current score reads 0.
  - **If this fails, the user sees:** The board looks empty or frozen, key presses scroll the page instead of moving tiles, or the New Game button does nothing.

- [x] `UI-E2` | MEDIUM | Add "You Win!" and "Game Over" overlays with continue and restart options (branch: `phase/ui-e2`)

  > Test: Force a 2048 tile into the model, trigger a re-render, and verify a visible "You Win!" overlay appears with a "Continue" button; force a loss state and verify a "Game Over" overlay with a "Try Again" button appears.

  **Entry Criteria:** Win/lose detection from CORE-E5 and DOM rendering from UI-E1 are working.
  **Exit Criteria:** When win state becomes true, a modal/overlay with "You Win!" and a "Continue" button is displayed; clicking "Continue" dismisses the overlay and allows further play; when loss state becomes true, a "Game Over" overlay with a "Try Again" button is displayed; clicking "Try Again" reinitializes the game; overlays block further tile movement input until dismissed or acted upon.
  **TDD Requirements:**
  - `tests/ui-e2.test.js`: Verifies injecting a 2048 tile into the model causes `.win-overlay` to be visible in the DOM
  - `tests/ui-e2.test.js`: Verifies clicking "Continue" hides the win overlay and subsequent arrow keys still function
  - `tests/ui-e2.test.js`: Verifies injecting a loss board causes `.game-over-overlay` to be visible
  - `tests/ui-e2.test.js`: Verifies clicking "Try Again" resets the board, score, and hides the overlay
  **Done Criteria:**
  - [ ] Win overlay appears with a Continue option when 2048 is reached
  - [ ] Game Over overlay appears with a Try Again option when no moves remain
  - [ ] Continue allows further play; Try Again restarts the game
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player sees a clear victory message when they reach 2048, with the choice to keep playing; they see a game-over message when stuck, with a button to start again.
  - **How we'll check:** Programmatically set the model to a win state, assert the `.win-overlay` element is visible and contains a "Continue" button; simulate a click on "Continue", assert the overlay is hidden and another move is accepted; repeat for loss state with "Try Again".
  - **If this fails, the user sees:** No message when they win or lose, or the game ends abruptly without explanation.

- [x] `UI-E3` | MEDIUM | Apply tile color theming by value and smooth CSS transitions for tile movement and merges (branch: `phase/ui-e3`)

  > Test: Inspect the DOM after spawning tiles of various values and confirm each has a distinct background color class; trigger a move and visually confirm (or assert via `getComputedStyle`) that tile elements transition their position or appearance smoothly.

  **Entry Criteria:** Tile rendering from UI-E1 and overlay logic from UI-E2 are functional.
  **Exit Criteria:** Each tile value (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048) has a distinct CSS background color and text color meeting WCAG 2.1 Level AA contrast; CSS `transition` properties animate tile position changes and appearance changes across moves; animations complete within a short, consistent duration (e.g., 100–200 ms).
  **TDD Requirements:**
  - `tests/ui-e3.test.js`: Verifies every tile value from 2 to 2048 has an associated CSS rule with a background color and a computed contrast ratio ≥ 4.5:1 against its text color
  - `tests/ui-e3.test.js`: Verifies `.tile` elements have a `transition` property affecting `transform` or relevant positional/opacity attributes
  **Done Criteria:**
  - [ ] Tiles have unique colors for each power-of-two value up to 2048
  - [ ] Text on every tile meets minimum contrast requirements
  - [ ] Tile movements and merges animate smoothly via CSS transitions
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player sees tiles in distinct colors that get richer as values grow, and tiles glide smoothly to new positions instead of jumping instantly.
  - **How we'll check:** Create tiles of each value in the DOM, compute contrast ratios via `getComputedStyle` for background and text colors, assert each is ≥ 4.5:1; trigger a move, assert `.tile` elements possess a non-zero `transition-duration` for positional properties.
  - **If this fails, the user sees:** All tiles look the same flat color, text is unreadable, or tiles snap jarringly from place to place.

- [x] `TEST-E1` | MEDIUM | Final cross-browser and edge-case verification checklist (branch: `phase/test-e1`)

  > Test: Manually exercise or script a checklist covering rapid key presses, `localStorage` disabled mode, win-then-continue flow, and full-board-merge-available flow.

  **Entry Criteria:** All prior phases are complete and functional in `index.html`.
  **Exit Criteria:** A `CHECKLIST.md` or equivalent automated tests verify: (1) rapid successive arrow key presses during the move animation do not corrupt board state or skip turns, (2) game loads and plays without error when `localStorage` is disabled, (3) a player who reaches 2048 and clicks Continue can continue playing and potentially reach higher tiles, (4) a full board with available merges does not trigger game over, (5) the total serialized game state fits well within typical `localStorage` quota.
  **TDD Requirements:**
  - `tests/test-e1.test.js`: Verifies queuing or debouncing rapid key presses does not produce an invalid board state compared to pressing the same keys slowly
  - `tests/test-e1.test.js`: Verifies the game initializes successfully when `localStorage` is stubbed to throw on all access
  - `tests/test-e1.test.js`: Verifies a win state followed by 10 additional valid moves remains in a playable state without unexpected loss/win toggles
  **Done Criteria:**
  - [ ] Rapid key press edge case is handled safely
  - [ ] `localStorage` disabled degradation is verified
  - [ ] Win-then-continue flow is verified end-to-end
  - [ ] Full-board-with-merges state does not falsely trigger game over
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player can mash arrow keys without breaking the game, play in incognito mode without crashes, keep going after winning, and is not wrongly told the game is over when a merge is still possible.
  - **How we'll check:** Dispatch 20 `keydown` events with `ArrowLeft` in a 50 ms burst, assert the final board state is valid and logically reachable; stub `localStorage`, reload, assert the game starts and a move can be made; force a win, click Continue, perform 10 more moves, assert no crash and overlays behave correctly; force a full but mergeable board, assert the loss overlay does not appear.
  - **If this fails, the user sees:** Tiles disappear or stack incorrectly after frantic key pressing, the game crashes in private browsing, the board freezes after choosing Continue, or a premature Game Over cuts the session short.