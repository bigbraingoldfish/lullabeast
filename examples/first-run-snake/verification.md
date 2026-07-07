# Verification

## Project type
game

## Entry point
- Command: `python3 -m http.server 8080`
- Ready signal: HTTP 200 from http://localhost:8080/index.html

## Prerequisites

### Tools
- Python 3 — HTTP server for serving the single HTML file — needed by all
- Node.js — Playwright test runner for acceptance tests — needed by all
- Chromium — headless browser for canvas inspection and input simulation — needed by all

### Environment
none

## Public surface
1. Open a single HTML file and see a start screen with title and controls hint
2. Press ENTER to start playing
3. Steer the snake using Arrow keys to navigate the board
4. Eat red food items to grow the snake and increase score
5. Experience the game speeding up as score increases
6. See a game over screen with final score and a retry prompt on collision
7. Press Y to restart the game or N to see a farewell message
8. Win the game by filling the entire 32x32 board with the snake

## Verification stack
- Acceptance tool: playwright
- Notes: The game renders all text and graphics on an HTML5 Canvas element, so DOM text assertions are insufficient; the reviewer uses page.evaluate() to inspect game state variables and canvas pixel data directly.
