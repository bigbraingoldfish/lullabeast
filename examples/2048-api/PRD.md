# 2048 Game

## Problem Statement

Players need an engaging, lightweight, and accessible single-player puzzle game that can be played in a browser without installation or complex setup. The existing solutions may be bloated, ad-heavy, or lack offline capability. A clean, minimal 2048 implementation provides a satisfying mental challenge with simple controls but deep strategy.

## Goals & Success Metrics

**Primary Goals:**
1. Implement a fully functional 2048 game matching the classic rules in a single-page web application.
2. Ensure smooth, responsive gameplay with keyboard controls (arrow keys).
3. Provide visual feedback for tile movements, merges, and new tile spawns.
4. Track and persist high scores locally across browser sessions.

**Success Metrics:**
- Game correctly implements all 2048 rules (4×4 grid, tile merging, scoring, win/lose conditions).
- New tile spawns follow the standard rules: 90% chance of "2", 10% chance of "4".
- Game state transitions (idle → playing → won → lost) are accurate.
- High score persists in `localStorage` and survives page reloads.
- Runs in modern browsers (Chrome, Firefox, Safari, Edge) without external dependencies.

## User Stories

1. **As a player**, I want to use arrow keys to move all tiles in the chosen direction, so that I can control the game intuitively.
2. **As a player**, I want identical tiles that collide during a move to merge into a single tile showing their sum, so that the core game mechanic works as expected.
3. **As a player**, I want a new tile (value 2 with 90% probability, or 4 with 10%) to appear in a random empty cell after every valid move, so that the game progresses.
4. **As a player**, I want the game to show my current score (sum of all merged tiles), so that I can track my performance.
5. **As a player**, I want the game to save my best score, so that I can compete with myself over multiple sessions.
6. **As a player**, I want to see a clear "You Win!" message when a tile with the value 2048 appears, and be able to continue playing if I choose.
7. **As a player**, I want to see a "Game Over" message when no valid moves remain, so that I know the game has ended.
8. **As a player**, I want a "New Game" button, so that I can restart at any time.

## Functional Requirements

### Core Game Logic

**Grid & Initial State**
- Fixed 4×4 grid (16 cells).
- Game starts with two tiles placed in random empty cells.
- Each initial tile has a 90% chance of being "2" and a 10% chance of being "4".

**Movement & Merge Mechanics**
A move consists of three distinct steps applied to each row (horizontal) or column (vertical) in the direction of travel:

1. **Compression — Slide toward the far edge**  
   Tiles in each line are shifted toward the side furthest from the movement direction, filling empty spaces but respecting existing tile positions. Empty cells (value 0) become trailing cells.
2. **Merge — Combine adjacent tiles from the far side**  
   Scanning from the far side of movement, when two adjacent tiles have the same value, they merge into a single tile of double value. This merged tile occupies the cell closer to the far side. The original second cell is cleared (value 0). Each tile can merge at most once per move.
3. **Re-compression**  
   After merges create new empty cells, compress again so no gaps remain between tiles and the far edge.

**Merge Order — Critical Rule**  
When multiple merges are possible, they occur from the far side (direction of travel) first:
- Board row `[2, 2, 2]` moved **left** becomes `[4, 2, 0]` (first two 2s merge at the far-left; the remaining 2 has no partner).
- Board row `[2, 2, 2, 2]` moved **left** becomes `[4, 4, 0, 0]` (first pair from left merges, second pair from left merges).
- Board row `[2, 2, 4, 4]` moved **left** becomes `[4, 8, 0, 0]`.

**No Double-Merge Rule**  
A tile created by a merge during the current move is disqualified from merging again in that same move. This is naturally enforced by the two-pass approach (merge then compress, not merge while compressing). A merged tile cannot produce a chain reaction like `[2, 2, 4]` → `[8]`.

**Scoring**
- Each merge adds the value of the newly created tile (the sum of the two merged tiles) to the current score.
- Example: merging two `4` tiles adds `8` points to the score.

**New Tile Spawn**
- After every **valid move** (one that changes the board state — any tile moved or merged), exactly one new tile spawns.
- Spawn in a randomly chosen empty cell (value 0).
- Spawn value: 90% chance of "2", 10% chance of "4".
- If an invalid move (no change to board) is attempted, no new tile spawns and the move is ignored.

**Win Condition**
- Detected when any tile reaches the value 2048.
- Player is notified with a "You Win!" overlay.
- Gameplay may continue in "keep going" mode if the player chooses.

**Loss Condition**
- The game ends when no empty cells remain **and** no adjacent tiles of equal value exist in any direction (no valid merges possible).
- "Game Over" overlay shown.

### Input Handling
- Accept keyboard arrow keys (`ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight`) for tile movement.
- Ignore input during animations to prevent state corruption.
- Prevent default browser scrolling behavior when arrow keys are pressed.

### UI Features
- Display current score prominently during gameplay.
- Display best (high) score, persisted in `localStorage`.
- Button to start a new game at any time.
- Overlay notifications for "You Win!" and "Game Over" states, with options to continue or restart.
- Visual styling for tiles differentiated by value (color intensity increases with value).
- Smooth CSS transitions for tile movements and merges.

## Edge Cases

1. **No Empty Cells, But Moves Available**: The game must not end if tiles can still merge, even when the grid is full.
2. **Invalid Move Prevention**: If an arrow key is pressed but no tiles move or merge, no new tile spawns, and the move is considered invalid.
3. **Merge Chain Blocking**: A tile resulting from a merge in the current move cannot merge again during that same move (e.g., `[2, 2, 4]` moving left must become `[4, 4]`, not `[8]`).
4. **Simultaneous Merge Order**: When multiple merges are possible in a single direction, merges must happen from the far side of the direction of movement first (e.g., `[2, 2, 2]` left becomes `[4, 2]`, `[2, 2, 2, 2]` left becomes `[4, 4]`).
5. **Score Reset on New Game**: Starting a new game must reset only the current score; the best score should persist.
6. **Rapid Key Presses**: Rapid successive key presses during animations must be debounced or queued safely to avoid skipping turns or corrupting state.
7. **Browser `localStorage` Unavailable**: If `localStorage` is disabled or unavailable, the game must degrade gracefully (best score resets each session) without throwing errors.
8. **Tile Spawn Location**: New tiles must only spawn in genuinely empty cells after a valid move.

## Non-Functional Requirements

1. **Performance**: Game logic must execute in < 16ms per move to maintain 60fps perceived responsiveness; CSS animations should run smoothly on modern hardware.
2. **Browser Compatibility**: Must function in the last two major versions of Chrome, Firefox, Safari, and Edge.
3. **Accessibility**: Game board and controls must be operable via keyboard. Minimum WCAG 2.1 Level AA contrast ratios for text on tile backgrounds.
4. **Offline Capability**: The entire game must work without an internet connection once loaded.
5. **Single File Deployment**: The game should be deployable as a single `.html` file containing inlined CSS and JavaScript, requiring no build step or external dependencies.
6. **State Size**: Game state must be small enough to serialize into `localStorage` without exceeding typical quota limits (~5MB).

## Dependencies & Integrations

- **No external runtime dependencies**: The game logic and rendering must be implemented using native Web APIs (HTML5, CSS3, Vanilla JavaScript).
- **Persistence**: `window.localStorage` for high-score storage.
- **No backend services**, APIs, or environment variables required.
- **Build prerequisites for development**: Any standard web browser for testing.

## Milestones & Timeline

1. **Phase 1 — Core Grid & Rendering (0-30%)**
   - Render a 4×4 CSS grid representing the board.
   - Implement data model for grid cells and tile values.
   - Implement random tile spawning logic (90/10 rule).

2. **Phase 2 — Movement & Merging Logic (30-60%)**
   - Implement directional sliding for all four directions.
   - Implement merge logic respecting the "no double merge per move" rule.
   - Implement scoring (sum of merged values) and detect valid vs. invalid moves.

3. **Phase 3 — Win/Lose Detection & State Management (60-85%)**
   - Implement win detection (tile value ≥ 2048).
   - Implement loss detection (no empty cells + no possible merges).
   - Implement `localStorage` persistence for best score.
   - Add "New Game" functionality with proper state reset.

4. **Phase 4 — Polish & UI Completion (85-100%)**
   - Add CSS animations/transitions for tile movement and merges.
   - Implement Win/Game Over overlays with continue/restart options.
   - Apply distinct tile colors per value.
   - Final cross-browser testing and edge-case verification.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Animation desync** and state corruption from rapid key presses | High | Lock input during animation transitions and/or implement a move queue with debouncing. |
| **Subtle merge logic bugs** leading to non-standard behavior | Medium | Write exhaustive unit-style tests for edge-case board configurations (e.g., `[2,2,2]`, `[2,2,2,2]` in all directions) before final integration. |
| **Browser `localStorage` quota exceeded or disabled** | Low | Wrap all `localStorage` access in `try/catch`; silently degrade to session-only high score. |
| **CSS grid rendering inconsistencies across browsers** | Low | Use well-supported CSS Grid properties; test on target browsers. Avoid complex nested transform animations on the grid container itself. |

## Open Questions

*None — requirements clarified through direct project specification.*

## Glossary & Domain Terms

- **Grid / Board**: The 4×4 playing area containing tiles.
- **Tile**: A numbered square on the board.
- **Merge**: The action of two tiles with the same value combining into one tile with double the value.
- **Spawn**: The appearance of a new tile on the board after a valid move.
- **Valid Move**: A move that results in at least one tile changing position or merging.
- **Game Over**: The state where no empty cells exist and no merges are possible.
- **Win State**: The state where at least one tile has reached the value 2048.
- **localStorage**: A Web Storage API mechanism for persisting data in the browser across sessions.

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-06-17 | Assistant | Initial PRD draft created based on user specification. Defined all 12 sections with assumptions around web-based, single-file HTML/CSS/JS deployment. |
| 2026-06-20 | Assistant | Expanded Functional Requirements with detailed three-step movement logic (compress → merge → re-compress), merge order rules, no-double-merge rule, and clarified spawn conditions. Added concrete board-state examples for edge-case merges. |

> ✅ PRD CONVERSION-READY