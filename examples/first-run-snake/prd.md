# Snake Minimal

A single-file, browser-based Snake game. Zero dependencies, zero build step. Designed as a
"hello world" pipeline project: readable, runnable, and completable in 4 phases.

---

## Problem Statement

New users need a tiny, complete game they can open in a browser, read in one sitting, and
extend without wrestling with build tools, frameworks, or complex state management. The game
should demonstrate core concepts (game loop, collision, state machines, input handling) in
the simplest possible form.

---

## Goals & Success Metrics

1. **Runs instantly**: open the HTML file in any modern browser and play immediately.
2. **Readable code**: a single file; every function fits on screen without scrolling.
3. **4 build phases**: each phase adds a visible, testable chunk of behavior.
4. **No external dependencies**: pure HTML5 Canvas plus vanilla JavaScript.
5. **Success metric**: a new user can trace the code top-to-bottom and understand the full
   flow in under 10 minutes.

---

## User Stories

- **As a player**, I want a start screen that tells me the controls, so I know how to play
  before the snake starts moving.
- **As a player**, I want to use the arrow keys to move the snake so I can navigate the board.
- **As a player**, I want the snake to grow when it eats food so I have a sense of progression.
- **As a player**, I want the game to speed up as my score increases so the difficulty ramps.
- **As a player**, I want to see "Try again? (Y/N)" when I die so I can restart or quit.
- **As a learner**, I want the entire game in one file so I can read it without jumping
  between modules.

---

## Functional Requirements

### FR-1: Game Board
- Render a square grid of 32x32 cells.
- Each cell is 20x20 screen pixels.
- Canvas size: 640x640 px, black background (#000000).

### FR-2: Snake
- Snake is a list of `{x, y}` grid coordinates.
- Initial length: 3 cells, at grid positions (14,15), (15,15), (16,15), head at (16,15),
  moving right.
- Render each segment as a green (#00FF41) filled rectangle, 18x18 px centered in its
  20x20 cell (1 px gap). The head may use a slightly brighter green (#00FF66) for optional
  visual distinction.

### FR-3: Food
- One food item exists at any time.
- Render as a red (#FF0044) filled shape centered in its cell.
- Spawn at a random grid cell not currently occupied by the snake.

### FR-4: Start Screen
- On page load, render a start screen on the canvas: "SNAKE" (green, large, centered),
  "Controls: Arrow Keys" below it, and "Press ENTER to start" below that.
- ENTER starts the game. All other keys are ignored before the game starts.

### FR-5: Movement
- Tick-driven movement via `setInterval`. Default tick: 200 ms.
- Accept arrow keys (Up, Down, Left, Right) to change direction.
- Prevent 180 degree instant reversals (moving right, then pressing left, is ignored).
- On each tick, add a new head cell in the current direction and remove the tail cell
  (unless eating, see FR-6).
- Only the first valid directional input per tick is processed; it is buffered and applied
  on the next tick.

### FR-6: Eating & Growth
- If the new head cell equals the food cell:
  - Do not remove the tail (snake grows by 1).
  - Increment score by 1.
  - Spawn new food at a random unoccupied cell.
  - **Speed up**: reduce the tick interval by 5% per food eaten.
  - Minimum tick: 50 ms (hard floor).

### FR-7: Collision & Game Over
- Game over occurs if the new head:
  - Is outside the 32x32 grid (wall collision: x < 0, x > 31, y < 0, or y > 31), **or**
  - Occupies any existing snake segment (self-collision).
- On game over: stop the game loop and render, centered on the canvas: "GAME OVER",
  "Score: {n}", and "Try again? (Y/N)".
- Y restarts (full reset of snake, score, tick interval, and food). N renders
  "Thanks for playing!" for 2 seconds, then the final frame freezes.

### FR-8: Score Display
- During play, show the live score as "Score: {n}" in the top-left corner of the canvas,
  white (#FFFFFF), monospace font.
- **No high-score persistence**: score resets every session (shortest code path).

---

## Edge Cases

| # | Scenario | Expected Behavior |
|---|--------|----------------|
| EC-1 | Food spawns on the snake | Re-roll spawn until an empty cell is found |
| EC-2 | Player presses opposite direction rapidly | Ignore the reversal; current direction persists |
| EC-3 | Snake fills the entire board (max length 1024) | Treat as win: stop loop, show "YOU WIN!" with "Play again? (Y/N)" |
| EC-4 | Key pressed before game starts | Ignored, except ENTER (which starts the game) |
| EC-5 | Multiple keys pressed in one tick | Only the first valid directional input per tick is processed |
| EC-6 | Canvas resize / unsupported browser | Static 640x640 canvas; no dynamic scaling required |
| EC-7 | Y or N pressed during active gameplay | Ignored (Y/N only act on the game-over and win screens) |
| EC-8 | N pressed at game over | "Thanks for playing!" shows for 2 seconds, then the final frame freezes |

---

## Non-Functional Requirements

1. **Portability**: one `.html` file; opens in Chrome, Firefox, Safari, Edge.
2. **Performance**: game loop uses `setInterval`; no requestAnimationFrame needed at this
   tick rate.
3. **Accessibility**: minimal; listen on `window` for keyboard events. No screen-reader
   optimization required at this level.
4. **File size**: a single HTML file under 15 KB, inline CSS and JS.
5. **No build tools**: no npm for the game itself, no bundler, no transpiler. Plain ES6.

---

## Dependencies & Integrations

None. Pure browser APIs only: no external libraries, no CDN links, no environment variables.
(The acceptance tests use Playwright, which is a test-time dependency, not a game dependency.)

---

## Milestones & Timeline

### Phase 1: Draw the Board & Snake
- Render the 640x640 canvas with the black background.
- Draw the initial 3-segment snake at center.
- No movement, no input. Verify the snake renders correctly.

### Phase 2: Movement & Input
- Add arrow-key input handling.
- Add tick-based movement (head advances, tail shrinks).
- Verify the snake moves and responds to all four directions, and reversals are blocked.

### Phase 3: Food, Eating, Growth & Speed
- Spawn food at random unoccupied cells.
- Detect head-food collision, grow snake, increment score.
- Reduce the tick interval 5% per food eaten (floor 50 ms).
- Render the score on the canvas.

### Phase 4: Start Screen, Game Over & Restart
- Add the start screen (ENTER to begin).
- Implement wall and self collision checks.
- On collision: stop the loop, show "GAME OVER", the final score, and "Try again? (Y/N)".
- Y resets everything and restarts; N shows the farewell message and freezes.
- Cover the win condition and all edge cases (EC-1 through EC-8).

**Total estimated effort**: 4 phases, each a small self-contained step.

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Food spawn re-roll loops forever when the snake is almost full | Low | Medium | Cap retries at 2000; if exceeded, trigger EC-3 (win condition) |
| Keyboard input feels laggy at fast tick rates | Medium | Low | Keep the minimum tick at 50 ms; buffer only the first valid input per tick |
| Learners find compressed single-file code hard to follow | Low | Medium | Generous inline comments; one function per major behavior |

---

## Resolved Decisions

1. No pause feature. Absolutely minimal control surface: arrows, ENTER, Y, N.
2. Score is drawn directly on the canvas (no DOM text element).
3. Restart is instant, no countdown.
4. No high-score persistence (no localStorage).

---

## Glossary & Domain Terms

| Term | Definition |
|------|------------|
| Cell | One square in the 32x32 grid; 20x20 screen pixels |
| Tick | One discrete step of the game loop; the snake moves one cell per tick |
| Segment | One coordinate pair in the snake's body array |
| Grid coordinates | Integer x,y where 0 <= x,y <= 31 (32x32 grid) |
