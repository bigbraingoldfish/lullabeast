# Snake Minimal Roadmap

- [ ] `CORE-E1` | CRITICAL | Canvas, grid & snake rendering (branch: `phase/core-e1`)

  > Test: Open index.html in a browser and verify a 640x640 black canvas renders with a 3-segment green snake centered at grid positions (14,15), (15,15), (16,15).

  **Entry Criteria:**
  No prior phases required. Empty project directory ready for file creation.

  **Exit Criteria:**
  `index.html` exists as a single file under 15 KB, contains a `<canvas>` element with width=640 height=640, initializes a snake array of 3 `{x,y}` coordinate objects at positions (14,15),(15,15),(16,15), renders the snake as green filled rectangles on a black background, and contains no external dependencies (no CDN links, no external CSS/JS files).

  **TDD Requirements:**
  - `test/core-e1-canvas-render.spec.js`: Loads index.html in headless chromium, asserts `<canvas>` element exists with width=640 and height=640 attributes, reads canvas pixel data at positions corresponding to grid cells (14,15),(15,15),(16,15) and asserts green channel is dominant (snake rendered), asserts pixel at (0,0) is rgb(0,0,0) (black background).

  **Done Criteria:**
  - [ ] `index.html` is a single file with inline CSS and JS, under 15 KB
  - [ ] Canvas element has width=640, height=640 attributes
  - [ ] Canvas background is black (#000000)
  - [ ] Snake array initialized with 3 segments at positions (14,15),(15,15),(16,15)
  - [ ] Snake segments rendered as green (#00FF41) filled rectangles, 18x18 px centered in 20x20 cells (1px gap)
  - [ ] Snake head may use slightly brighter green (#00FF66) for optional visual distinction
  - [ ] No external dependencies (no CDN, no external files)
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  <!-- Assumed: snake head color distinction is optional per PRD FR-2 ("optional visual distinction") -->
  <!-- Assumed: instant start/restart with no countdown, per PRD Resolved Decision 3 -->

  **Behavioral Verification:**
  - **User-observable:** A player can open the HTML file and see a black game board with a short green snake in the center.
  - **How we'll check:** run `npx playwright test test/core-e1-canvas-render.spec.js`, expect exit code 0; the test loads index.html in headless chromium, asserts `<canvas>` width=640 height=640, reads pixel data at canvas positions corresponding to grid cells (14,15),(15,15),(16,15) and asserts green channel dominant (snake rendered), asserts pixel at (0,0) is rgb(0,0,0) (black background).
  - **If this fails, the user sees:** The game board doesn't appear or the snake isn't visible when opening the file.

- [ ] `CORE-E2` | CRITICAL | Movement & input handling (branch: `phase/core-e2`)

  > Test: Open index.html, verify the snake moves right automatically on page load, press Arrow keys to change direction, and verify 180 degree reversals are ignored.

  **Entry Criteria:**
  `CORE-E1` complete. `index.html` contains canvas and snake rendering. Snake array and canvas context accessible via global scope for test inspection.

  **Exit Criteria:**
  Game loop runs via `setInterval` at 200ms default tick interval, snake moves one cell per tick in the current direction (right initially), Arrow keys change direction with 180 degree reversal prevention, full canvas clear and redraw occurs each tick, snake tail is removed each tick (length stays constant at 3 since no food exists yet), only first valid directional input per tick is processed.

  **TDD Requirements:**
  - `test/core-e2-movement-input.spec.js`: Loads index.html, waits 250ms (one tick), uses page.evaluate to assert snake head moved from (16,15) to (17,15) (moving right), presses ArrowUp, waits 250ms, asserts head y decreased by 1, presses ArrowLeft (reversal of right), waits 250ms, asserts head x still increasing (reversal blocked), presses ArrowDown then ArrowLeft in rapid succession within one tick, asserts only first valid direction applied (EC-5).

  **Done Criteria:**
  - [ ] `setInterval` game loop running at 200ms default tick interval
  - [ ] Snake moves one cell per tick in current direction (right initially)
  - [ ] Arrow Up/Down/Left/Right change direction
  - [ ] 180 degree reversal prevented (Right to Left, Up to Down, Left to Right, Down to Up all ignored)
  - [ ] New head computed correctly (Up: y-1, Down: y+1, Left: x-1, Right: x+1)
  - [ ] Tail removed each tick (snake length constant at 3 when no food)
  - [ ] Full canvas cleared and redrawn each tick
  - [ ] Only first valid directional input per tick processed (EC-5)
  - [ ] Input buffer stores latest buffered direction, applied on next tick
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player can press Arrow keys to steer the snake around the board, and the snake won't reverse direction instantly.
  - **How we'll check:** run `npx playwright test test/core-e2-movement-input.spec.js`, expect exit code 0; the test loads index.html, waits 250ms, uses page.evaluate to assert snake head x increased by 1 (moving right from initial position), presses ArrowUp, waits 250ms, asserts head y decreased by 1, presses ArrowLeft (reversal), waits 250ms, asserts head x still increasing (reversal blocked), presses ArrowDown+ArrowLeft within same tick window, asserts only first direction applied.
  - **If this fails, the user sees:** The snake doesn't move, doesn't respond to Arrow keys, or it reverses into itself when pressing the opposite direction.

- [ ] `CORE-E3` | HIGH | Food, eating, growth & speed (branch: `phase/core-e3`)

  > Test: Open index.html, verify a red food item appears on the board, navigate the snake to eat it, and verify the snake grows, score increments, and the game speeds up.

  **Entry Criteria:**
  `CORE-E2` complete. Snake movement and input handling work. Game loop, snake array, direction state, and tick interval accessible via global scope for test inspection.

  **Exit Criteria:**
  One food item rendered on canvas at a random unoccupied cell, food is red (#FF0044), eating detection works (head cell equals food cell), on eat: snake grows by 1 (tail not removed), score increments by 1, new food spawns at random unoccupied cell, tick interval reduces by 5% per food eaten (floor 50ms), score displayed on canvas in top-left corner as "Score: {n}" in white (#FFFFFF) monospace font, food respawns until empty cell found (EC-1).

  **TDD Requirements:**
  - `test/core-e3-food-eating.spec.js`: Loads index.html, uses page.evaluate to get food position and assert it is not on any snake segment, uses page.evaluate to get score and assert 0, sends Arrow keys to navigate snake toward food position, waits for eat event, asserts snake array length increased by 1, asserts score === 1, asserts new food position differs from previous, asserts tick interval reduced from 200 to approximately 190ms (5% reduction).

  **Done Criteria:**
  - [ ] One food item rendered as red (#FF0044) filled shape centered in cell
  - [ ] Food spawns at random cell not occupied by snake (EC-1: re-roll until empty cell found)
  - [ ] Eating detection: head cell equals food cell
  - [ ] On eat: tail not removed (snake grows by 1)
  - [ ] Score increments by 1 per food eaten
  - [ ] New food spawns at random unoccupied cell after eating
  - [ ] Tick interval reduced by 5% per food (floor at 50ms minimum)
  - [ ] Score displayed as "Score: {n}" in top-left corner, white (#FFFFFF), monospace font
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player can see a red food item on the board, steer the snake to eat it, watch the snake grow and the score increase, and notice the game getting slightly faster.
  - **How we'll check:** run `npx playwright test test/core-e3-food-eating.spec.js`, expect exit code 0; the test loads index.html, uses page.evaluate to read food position and assert it is not on any snake segment, reads score and asserts 0, sends Arrow keys to navigate snake to food position, waits for eat, asserts snake array length increased by 1, asserts score === 1, asserts new food position differs from old, asserts tick interval reduced from 200 to ~190ms.
  - **If this fails, the user sees:** Food doesn't appear, the snake doesn't grow when passing over food, or the score doesn't update.

- [ ] `UI-E1` | HIGH | Start screen, game over, restart & edge cases (branch: `phase/ui-e1`)

  > Test: Open index.html, verify a start screen with "SNAKE" title appears, press ENTER to start, play until collision, verify "GAME OVER" with score and "Try again? (Y/N)" appears, press Y to restart, then press N at next game over to see farewell message.

  **Entry Criteria:**
  `CORE-E3` complete. Food, eating, growth, speed, and gameplay score display all work. Game state (snake, score, tick interval, food, game mode) accessible via global scope for test inspection.

  **Exit Criteria:**
  Start screen renders on page load with "SNAKE" title, "Controls: Arrow Keys" subtitle, "Press ENTER to start" prompt (all canvas text, green on black), ENTER starts game, wall collision detected (x/y out of 0-31 bounds), self-collision detected (head in body array excluding head), on collision: clearInterval and render "GAME OVER" + "Score: {n}" + "Try again? (Y/N)" centered on canvas, Y key resets and restarts game, N key shows "Thanks for playing!" for 2 seconds then freezes, win condition (snake length 1024) shows "YOU WIN!" with "Play again? (Y/N)", Y/N keys ignored during active gameplay (EC-7), keys before start ignored except ENTER (EC-4), food spawn capped at 2000 retries before triggering win condition (EC-3 mitigation).

  <!-- Assumed: no localStorage high score persistence, per PRD Resolved Decision 4 -->

  **TDD Requirements:**
  - `test/ui-e1-start-gameover-restart.spec.js`: Loads index.html, uses page.evaluate to assert game state is 'start' (start screen active), presses Enter, asserts state becomes 'playing', sends Arrow keys to navigate snake into wall boundary, asserts state becomes 'gameover', presses 'y', asserts state becomes 'playing' and snake array reset to length 3 at positions (14,15),(15,15),(16,15) and score reset to 0, triggers game over again, presses 'n', asserts state becomes 'farewell', waits 2 seconds, asserts canvas frozen (no further state changes), presses 'y' during active gameplay, asserts state remains 'playing' (EC-7), presses random non-ENTER key before game start in a fresh load, asserts state remains 'start' (EC-4).

  **Done Criteria:**
  - [ ] Start screen renders on page load: "SNAKE" (green, large, centered), "Controls: Arrow Keys" (centered, below title), "Press ENTER to start" (centered, below subtitle)
  - [ ] ENTER key starts game (initializes state, starts game loop)
  - [ ] Wall collision detected (x < 0 || x > 31 || y < 0 || y > 31)
  - [ ] Self-collision detected (head coordinate exists in body array excluding head)
  - [ ] On collision: clearInterval, render "GAME OVER" (large, centered), "Score: {n}" (below), "Try again? (Y/N)" (below score)
  - [ ] Y key: full reset (snake, score, tick interval, food) and restart game loop
  - [ ] N key: show "Thanks for playing!" for 2 seconds, then freeze on final frame (EC-8)
  - [ ] Win condition: snake length 1024 triggers "YOU WIN!" with "Play again? (Y/N)" (EC-3)
  - [ ] Food spawn capped at 2000 retries before triggering win condition
  - [ ] Y/N keys ignored during gameplay (EC-7)
  - [ ] Non-ENTER/non-Arrow keys ignored before game starts (EC-4)
  - [ ] All text rendered on canvas via `fillText` with `textAlign = 'center'` and monospace font
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A player sees a start screen when opening the file, presses ENTER to play, and when they die they can press Y to try again or N to quit with a farewell message.
  - **How we'll check:** run `npx playwright test test/ui-e1-start-gameover-restart.spec.js`, expect exit code 0; the test loads index.html, uses page.evaluate to assert game state === 'start', presses Enter, asserts state === 'playing', sends Arrow keys to navigate snake into wall, asserts state === 'gameover', presses 'y', asserts state === 'playing' and snake array reset to length 3 at (14,15),(15,15),(16,15) and score === 0, triggers game over again, presses 'n', asserts state === 'farewell', waits 2s, asserts no further state changes, presses 'y' during active gameplay and asserts state remains 'playing', loads fresh page and presses non-ENTER key, asserts state remains 'start'.
  - **If this fails, the user sees:** The game starts immediately without a start screen, or dying doesn't show a retry prompt, or pressing Y doesn't restart the game.
