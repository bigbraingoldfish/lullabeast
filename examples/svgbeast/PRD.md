# SVG Pictionary

## Problem Statement

There is no easily accessible, self-contained demo that shows multiple multimodal AI models competing against each other in real time via a single API gateway. Existing AI demos are either single-agent chatbots, require backend infrastructure, or are not visually engaging enough to be shared as short-form video. SVG Pictionary closes this gap: a browser-based, zero-backend game where 3–4 AI agents (and optionally one human) compete in a Pictionary-style drawing and guessing contest, streamed live as SVG generation. The primary use case is portfolio demonstration and social sharing — it must be immediately watchable, understandable, and recordable at 1920×1080.

## Goals & Success Metrics

1. **Portfolio demo quality**: A 30-second screen recording posted to Reddit or Twitter must be immediately understood as "AI models competing in real time" by a non-technical viewer.
2. **Zero-backend self-hosting**: The entire app runs in a single HTML file or React SPA. No server, no database, no auth beyond an API key.
3. **Real-time multimodal coordination**: Drawing agent streams SVG element-by-element; guessing agents receive base64 canvas screenshots every 3s and respond with guesses. All API traffic goes through OpenRouter.
4. **Configurable AI roster**: Lobby allows selecting from a curated shortlist of 6 cost-efficient, high-throughput multimodal models on OpenRouter, enabling head-to-head model comparisons.
5. **Engaging game loop**: 3 rounds by default, 60s draw time, with fair rotation, linear score decay, and clear round/session end states.
6. **Session stability**: Game state survives accidental refresh via localStorage (API key, settings); used-word deduplication persists across "New Game" restarts within the same app session.

Success metrics:
- Game completes a full 3-round session without manual intervention in >95% of test runs.
- Screen recording at 1920×1080 renders all UI text legible at 720p playback.
- All 6 curated models can successfully participate as drawer or guesser when API key is valid.

## User Stories

**As a viewer (human participant)**
- I want to watch AI agents draw and guess in real time so that I can observe different model behaviors.
- I want to guess alongside the AI agents so that I feel part of the competition.
- I want to draw when my slot is selected so that I am a full participant, or configure an all-AI game so I can just watch.
- I want the app to remember my API key and settings across refreshes so that I don't reconfigure every time.

**As a content creator / portfolio demonstrator**
- I want to screen-record the game at 1920×1080 with a clean, dark, high-contrast UI so that the footage is shareable without editing.
- I want the game to auto-advance through rounds with a skippable countdown so that I can capture a full session in under 2 minutes.
- I want model names and avatars to be clearly visible so that viewers can identify which AI is which.
- I want the project to ship with an `.env.template` so that I can supply my OpenRouter API key securely via environment variable.

**As an AI agent (system behavior)**
- When I am the drawer, I receive the secret word and output SVG elements incrementally, obeying a strict no-text rule.
- When I am a guesser, I receive periodic base64 screenshots of the canvas and respond with a single-word or short-phrase guess.
- I have a persistent avatar, name, and color for the entire session so that I am identifiable.

## Functional Requirements

### 1. Lobby State

- **Layout**: Centered card layout on a dark background. Clean, uncluttered.
- **Tagline**: The tagline "Watch AIs fail at Pictionary... For now." is displayed prominently near the top of the lobby card. Must be eye-catching but not obnoxious or overly large — competitive, not childish.
- **OpenRouter API Key**:
  - The app first attempts to read the key from a build-time environment variable (`VITE_OPENROUTER_API_KEY`).
  - An `.env.template` file is included in the project root with a placeholder key variable.
  - A single text input in the lobby allows the user to override or provide the key manually if the env variable is absent. Masked (password type).
  - The manually entered key is stored in `localStorage` on valid entry. Pre-filled from `localStorage` if no env key is present.
  - **Key display rule**: The UI must never display the full unredacted API key. If the key must be shown (e.g., in a settings summary), display only the first 4 and last 4 characters with an ellipsis mask (e.g., `sk-or-...9a2b`).
- **Rounds setting**: Stepper control, range 1–10, default 3.
- **Draw time setting**: Slider with live value label, range 15–120 seconds, default 60.
- **Player slots**: Exactly 4 slots displayed as a horizontal row or 2×2 grid. Each slot shows:
  - Color-coded avatar (persistent for the session)
  - Slot name defaults: "Agent 1", "Agent 2", "Agent 3" for AI slots (before a model is chosen); "Squishy Human" for Human slots. The user may rename a Human slot at any time.
  - Toggle or dropdown to set slot type: AI or Human
  - If AI: model dropdown with fixed shortlist (see Dependencies). Default to first model. When a model is selected, the slot name auto-updates to that model's display name. If multiple AI slots select the same model, each duplicate is prefixed with a counter: "1 [DisplayName]", "2 [DisplayName]", "3 [DisplayName]" (e.g., "1 Gemini 3.1 FL", "2 Gemini 3.1 FL").
  - If Human: no model dropdown; label reads "Squishy Human"
- **Validation**: Exactly 4 slots must be configured as active (AI or Human). "Start Game" CTA is disabled until a valid API key and all 4 participants are configured.
- **Settings persistence**: Rounds, draw time, slot configurations (AI vs Human, model choices, names) are saved to `localStorage` and restored on app load.

### 2. Randomizer State

- Triggered at the start of every round.
- **Participant panels**: 4 compact panels arranged in a horizontal row or 2×2 grid. Each shows avatar, name, and a pulsing border highlight.
- **Slot-machine animation**: Highlight cycles rapidly through panels, decelerates over ~2 seconds, and locks on the selected drawer.
- **Selection rules**:
  - Track per-session draw history. Each participant must draw once before anyone draws a second time.
  - When all participants have drawn once, the draw history resets and a new cycle begins.
  - Only participants configured as "active" (AI or Human) are eligible.
- **Post-selection display**:
  - Central large text: "[Name] is drawing!"
  - If the selected slot is a Human: central area also reveals "Your word: [WORD]". No pass option — the human draws.
  - If the selected slot is an AI: central area shows only the name and "drawing next". Word is withheld from UI.
- Auto-advances to Drawing state after ~2s.

### 3. Drawing State

- **This is the primary screen. The canvas must dominate.**
- **Layout (desktop 1920×1080, 16:9)**:
  - **Canvas**: Large central element, occupies majority of horizontal width and height. Renders all accumulated SVG elements as a live hot-reload view (AI drawer), or displays the human's HTML5 `<canvas>` drawing surface.
  - **Secret word indicator**: A small label shows the word only to the drawer, placed **outside** the canvas element DOM and positioned visually near the canvas frame (e.g., above or beside it). It must be a sibling or descendant of a container that is NOT the canvas element being screenshotted, so that `canvas.toDataURL()`, `dom-to-image`, or `<foreignObject>` screenshot routines capturing the canvas element never include the word.
  - **Participant panels**: Compact horizontal strip placed **below** the canvas. 4 panels in a slim-height row — avatar, name, score, and any active speech bubble or "got it" badge.
  - **Guess history**: Chronological log placed to the **right** of the canvas. Compact text, newest at bottom, scrollable.
  - **Human guess input**: Text field + submit button, fixed at the bottom of the guess-history sidebar. Active whenever the user is not the drawer. Supports Enter-to-submit and visible submit button.
  - **Timer**: Prominent countdown bar or numeric display above or integrated into the canvas frame. Always visible. Updates every second. Turns **yellow** during the final 5 seconds as a visual warning.
- **AI Drawer behavior**:
  - System prompt includes: secret word, strict no-text rule, SVG-only output format.
  - **SVG positioning instruction**: The system prompt must explicitly instruct the agent to generate SVG elements centered within the viewBox and at a large enough scale to be clearly visible (e.g., "center your drawing around coordinates (400,300)" or "use a viewBox of 0 0 800 600 and draw objects between 100–700 on the X axis and 50–550 on the Y axis"). The app renders the SVG with `preserveAspectRatio="xMidYMid meet"` so that content is centered and scaled to fit the canvas container.
  - **No-text rule (hard enforced)**: Agent may not use `<text>`, `<tspan>`, letter-shaped paths, numbers, or readable characters. Violation = drawer forfeits all drawer points for the round.
  - **No-text detection**: Before appending each SVG chunk to the canvas, parse it into a temporary DOM fragment and check for `<text>`, `<tspan>`, or any element containing visible character data. If detected, flag the violation.
  - Outputs raw SVG elements sequentially — one element or small group per completion call.
  - Each new element is appended to the live canvas immediately.
  - **Streaming mode**: `stream: false` — each SVG element is generated by a single non-streaming completion call. The response text is parsed for valid SVG markup.
  - **Cadence**: One completion call per element/group. Wait minimum **500ms** between calls. Canvas updates immediately on each append.
  - **Timeout handling**: If a completion call exceeds 30s, skip to next element attempt. After 3 consecutive timeouts, mark drawer as failed for the round and end the round.
  - **Malformed SVG handling (3-strike rule)**: If an AI returns unparseable or malformed SVG, increment a malformation counter for that agent. If the counter reaches 3 in the same round, the agent forfeits — round ends, drawer gets 0 points, secret word is revealed, transition to Round End. If malformed count is <3, skip the bad output and continue with the next element attempt.
- **Human Drawer behavior**:
  - When human slot is selected:
    - Canvas mode switches to an interactive HTML5 `<canvas>` element (not SVG paths).
    - Tools: pencil, pen (smooth stroke), eraser, bucket/flood-fill (fills a contiguous area with the selected color), color palette, line width selector, undo (last stroke), clear (reset canvas).
    - **Color palette**: A fixed row of 12 preset color swatches (no custom hex input). Default palette: Black `#000000`, Dark Gray `#404040`, White `#FFFFFF`, Red `#FF3B30`, Orange `#FF9500`, Yellow `#FFCC00`, Green `#34C759`, Teal `#5AC8FA`, Blue `#007AFF`, Purple `#AF52DE`, Pink `#FF2D55`, Brown `#A2845E`. Tapping a swatch sets the active drawing color.
    - Drawing is rendered as raster strokes on the `<canvas>` in real time. Mouse input only (desktop-first target).
    - Human does NOT guess that round.
    - The secret word is displayed above or beside the canvas frame, not inside the `<canvas>` element — it is never included in the base64 PNG screenshot (`canvas.toDataURL()`) sent to AI guessers.
    - AI guessers receive a base64 PNG screenshot of the human's canvas (via `canvas.toDataURL()` or similar).
- **Guessing behavior (all non-drawing participants)**:
  - **Screenshot timing**: Every 3 seconds OR on each new SVG element added, whichever is **less** frequent. Minimum 1 second gap between sends (debounce).
  - **No-blank guard**: Before sending a screenshot to any AI guesser, verify that the canvas contains at least one visible drawing element. If the canvas is completely blank/empty (no SVG elements appended; all pixels transparent/white for human canvas), skip the send. Do not invoke API calls against a blank canvas. Re-check on the next scheduled interval.
  - Capture current canvas as base64 PNG. The capture must be scoped to the canvas element only (or equivalent SVG-to-image conversion of the SVG canvas subtree only), never including the secret word label or adjacent UI.
  - Send to each non-drawing AI agent in parallel (`Promise.all`): screenshot + system prompt establishing game context (no word, no hints).
  - Each guess request must also include a **running guess history** message containing every prior incorrect guess that agent has made during the current round. The system prompt must explicitly instruct: "Do not repeat any guess you have already submitted this round. Here is your guess history: [list]."
  - Agents respond with a short guess string.
  - **Correct guess detection**:
    - Compare against the word's `accepted` list (exact + semantic matches).
    - For AI agents: no fuzzy/misspelling tolerance.
    - For human guesses: apply fuzzy + misspelling tolerance using the word's `humanFuzzy` list.
  - **On correct match**:
    - Do NOT display the actual guess text publicly.
    - Show "[Agent Name] got it! 🎯" or "You got it! 🎯" with a timestamp.
    - Add time-appropriate points silently.
    - Display a badge on the participant's panel.
    - No sound effects or additional visual cues beyond the badge — keep it simple.
  - **On incorrect guess**:
    - AI agents: display as a brief speech bubble near the agent's panel below the canvas (positioned so it never overlaps or blocks the drawing area), auto-dismiss after ~2s, then the guess text appears in the guess-history sidebar.
    - Human: incorrect guess appears directly in the guess history sidebar only (no speech bubble).
  - **Round end trigger**: Timer expires OR all guessers have answered correctly.
- **Guess deduplication (human)**: Human guesses are also maintained in a session-scoped deduplication set. If a human types a duplicate, show a gentle inline warning and do not send the duplicate.

### 4. Round End State

- Clean centered layout.
- **Large word reveal**: "The word was: [WORD]" with brief entrance animation.
- **Ranked scoreboard**: 1st–4th with names, current total scores, and delta earned this round.
- **Auto-advance**: 5-second countdown to next round (or Session End if final round). User may click to skip.
- Minimal UI — this screen is brief.

### 5. Session End State

- **Podium layout**: 2nd place left, 1st place center (elevated), 3rd place right. Each shows avatar, name, and final score.
- **Stat callouts**: Below podium, 4 compact stats in a horizontal row:
  - Fastest guess (who guessed correctly in the shortest time)
  - Best drawer (who earned the most drawer points)
  - Most rounds won (who had the highest score in the most rounds)
  - Average guess time (mean time-to-correct-guess across all rounds)
- **"New Game" button**: Returns to Lobby. Preserves settings (rounds, draw time, slot configs, API key). Does NOT reset the used-word pool — words already used remain excluded.
- Nothing else on this screen.

### 6. Scoring System

- **Maximum guesser points**: 200 at second 0 of the draw period.
- **Linear decay**: Points degrade to a minimum of 10 at the end of the draw period.
- **Formula**: `points = Math.max(10, Math.round(200 - (elapsed / drawTime) * 190))`
- All correct guessers receive their time-appropriate score (ties allowed — each gets the clock value at their guess moment).
- **Drawer scoring**: +40 points for each agent/player that guesses correctly. Incentivizes clear drawing.
- No first-guess bonus; simultaneous correct guesses receive identical scores.

### 7. Word List

- Pre-baked into the app as a hardcoded JavaScript array.
- Minimum 50 entries, maximum ~60.
- Predominantly single-word clues; two-word entries limited to 20–25% of the list.
- No three-word entries.
- Words must be drawable as SVG — concrete, visual subjects (objects, animals, actions, scenes).
- Each entry structure:
  ```json
  {
    "word": "lighthouse",
    "accepted": ["lighthouse", "light house", "beacon", "watchtower"],
    "humanFuzzy": ["lighthous", "lite house", "lighhouse"]
  }
  ```
- **Session deduplication**: On app load, the word list is shuffled. Words used in any game this app session are excluded from selection. When all words are exhausted, the pool resets and reshuffles.
- "New Game" does NOT reset the used-word pool; only a full page reload does.

### 8. UI / Visual Design

- **Desktop-first, 1920×1080 (16:9)**. Designed for screen recording and social video sharing.
- Dark theme — game-night aesthetic, high contrast, readable at a glance.
- Each participant has a persistent distinct color used across all states: avatar fill, border, score text, speech bubble accent, guess log dot.
- Canvas is always the visual anchor on the Drawing screen; participant panels are supporting UI placed below.
- Speech bubbles: pop animation (scale up + fade in), 2s auto-dismiss, colored to match the agent. Positioned near the panel below the canvas — never overlapping the drawing area.
- Randomizer animation: border highlight cycles fast then decelerates onto winner — slot-machine feel.
- Word reveal at round end: large, satisfying, brief entrance animation (scale + fade).
- Timer: turns yellow in the final 5 seconds as a warning.
- Typography: readable, slightly sharp — competitive, not childish. Sans-serif, weights 400–700.
- **Lobby tagline**: "Watch AIs fail at Pictionary... For now." displayed at launch to encourage viewers to stop and watch.

### Verbatim Strings

The following literal strings must appear character-for-character in the built application (UI labels, buttons, error messages, prompts, or config files):

- "Watch AIs fail at Pictionary... For now." — lobby tagline displayed at launch
- "Agent 1" — default name for slot 1
- "Agent 2" — default name for slot 2
- "Agent 3" — default name for slot 3
- "Squishy Human" — default name for slot 4 / label for human slots
- "Start Game" — lobby primary CTA button
- "All 4 slots must be filled." — validation message shown when the user tries to start with fewer than 4 participants
- "is drawing!" — suffix used in the randomizer result display (e.g., "[Name] is drawing!")
- "Your word:" — prefix shown to the human drawer revealing their secret word
- "drawing next" — label shown when an AI is selected to draw (word withheld)
- " got it! 🎯" — suffix appended to an agent or player name on the correct-guess badge (includes leading space)
- "You got it! 🎯" — full badge text shown when the human guesses correctly
- "The word was:" — prefix for the round-end word reveal
- "New Game" — button on the Session End screen to return to Lobby
- "thinking..." — intermediate state label on an agent panel while awaiting an API response
- "unavailable" — terminal state label on an agent panel after repeated API failures
- "Gemini 3.1 FL" — display name in the model dropdown
- "Mistral S4" — display name in the model dropdown
- "GPT 4o mini" — display name in the model dropdown
- "Qwen 3.6 35B" — display name in the model dropdown
- "Step 3.7 F" — display name in the model dropdown
- "Nova 2 L" — display name in the model dropdown
- "# OpenRouter API Key (optional — can also be entered in the lobby)" — `.env.template` comment line
- "VITE_OPENROUTER_API_KEY=your_api_key_here" — `.env.template` placeholder line
- "VITE_OPENROUTER_API_KEY" — environment variable name read at build time
- "https://openrouter.ai/api/v1" — OpenRouter API base URL
- "/chat/completions" — OpenRouter chat completions endpoint path
- "Authorization" — HTTP header name for API key
- "Bearer " — Authorization header prefix (includes trailing space)
- "google/gemini-3.1-flash-lite" — OpenRouter model ID
- "mistralai/mistral-small-2603" — OpenRouter model ID
- "openai/gpt-4o-mini" — OpenRouter model ID
- "qwen/qwen3.6-35b-a3b" — OpenRouter model ID
- "stepfun/step-3.7-flash" — OpenRouter model ID
- "amazon/nova-2-lite-v1" — OpenRouter model ID
- "You have already guessed that. Try something else!" — inline duplicate-guess warning shown when a human submits a repeated guess during a round
- "Do not repeat any guess you have already submitted this round. Here is your guess history:" — text that must appear in guessing prompts to discourage duplicate AI guesses

## Edge Cases

- **All 4 slots configured as AI, no human**: Game runs fully autonomously. Human guess input is hidden. Human can still watch and screen-record.
- **Exactly 1 slot configured**: "Start Game" button is disabled. UI shows a message: "All 4 slots must be filled."
- **Human configured but never selected to draw in a given session**: Valid. The draw rotation still enforces fairness across all active participants.
- **AI drawer outputs text in SVG (no-text rule violation)**: Canvas still renders the valid SVG; the drawer is flagged and loses all drawer points for the round. Round continues if there is valid SVG to guess from; otherwise forfeit.
- **AI drawer completion times out (>30s)**: Skip to next element attempt. After 3 consecutive timeouts, mark drawer as failed and end the round.
- **AI drawer returns malformed SVG (3-strike rule)**: Increment the agent's malformation counter. On the 3rd malformed output in the same round, the agent forfeits — round ends, drawer gets 0 points, secret word revealed, transition to Round End. If malformed count is <3, skip the bad output and attempt the next element.
- **OpenRouter returns 429 (rate limit)**: Back off 5 seconds before retry. Retry up to 3 times. If still failing, mark the agent as silent for that round (no guesses / no more drawing).
- **All guessers fail to guess correctly before timer expires**: Round ends normally. Drawer gets 0 drawer points. No guesser points awarded.
- **Canvas is empty when a screenshot is sent** (e.g., first screenshot before AI has output anything): With the no-blank guard in place, no screenshot should be sent to AI guessers while the canvas is completely empty. If the guard fails for any reason and a blank image is sent, guessing agents receive a blank/white image and will likely guess incorrectly. This is acceptable — no special handling needed.
- **AI guesser repeats a previous guess despite the history instruction**: Track per-round guess history per agent. If the model outputs a duplicate string, silently treat it as incorrect (do not display a speech bubble or log it again), and do not deduct points. Continue waiting for the next non-duplicate guess.
- **AI guesser returns an empty response or whitespace-only guess**: Log to console, skip the response, and treat as a failed attempt. Do not display anything in the UI or the guess history. Resume the guessing loop on the next screenshot interval.
- **Human refreshes page mid-game**: Game state is lost (not persisted). App returns to Lobby with settings restored from localStorage. This is acceptable — no mid-game persistence required.
- **Word pool exhausted mid-session**: If all words have been used, the pool resets and reshuffles immediately. The user does not see an interruption.
- **Invalid or missing API key on Start Game**: Display inline error below the API key field. "Start Game" remains disabled.
- **localStorage cleared / unavailable**: App falls back to in-memory state. User must re-enter API key and settings.
- **All AI guessers become unavailable simultaneously (e.g., API outage)**: Round ends immediately. Drawer gets 0 drawer points. No guesser points awarded. Transition to Round End.

## Non-Functional Requirements

- **Performance**: Canvas screenshot (to base64 PNG) must complete in <500ms on a modern desktop browser. SVG append and re-render must feel instantaneous (no perceptible lag).
- **Rate limit protection**: Minimum **500ms** between AI drawer completion calls. Minimum 1s between screenshot sends to guessing agents. Exponential-ish backoff on 429s (5s, then 10s, then 15s).
- **Accessibility**: All interactive elements have visible focus states. Color contrast ratios meet WCAG AA for text on dark backgrounds.
- **Browser compatibility**: Latest Chrome, Firefox, Safari, Edge. No IE support required.
- **No external assets**: All fonts, icons, and word data are bundled or inline. The app must work offline after first load (except API calls).
- **Bundle size**: If built as a bundled SPA, target <2MB total initial load.
- **Security**: OpenRouter API key is stored in `localStorage` as a convenience fallback; the preferred method is an environment variable at build time. An `.env.template` file is provided in the project root. The UI must never display the full unredacted key.

## Dependencies & Integrations

### OpenRouter API

- **Endpoint**: `https://openrouter.ai/api/v1`
- **Authentication**: Bearer token in `Authorization` header, from user-supplied API key.
- **Chat completions endpoint**: `/chat/completions` (OpenAI-compatible messages format).
- **Streaming**: Drawing agent uses `stream: false` (one non-streaming completion call per SVG element). Guessing agents use non-streaming calls (single guess string).
- **Error handling**: 429 → back off and retry. 4xx/5xx → log to console, mark agent as failed for the round, surface minimal UI (agent panel shows "thinking..." → "unavailable").

### Environment Configuration

- The project ships with an `.env.template` file in the root directory containing:
  ```
  # OpenRouter API Key (optional — can also be entered in the lobby)
  VITE_OPENROUTER_API_KEY=your_api_key_here
  ```
- At build time, the app attempts to read the API key from the environment variable. If present, it is injected into the bundle and the lobby input is pre-filled.
- If the env variable is absent, the user must enter the key manually in the lobby; it is then saved to `localStorage`.
- **Build pipeline note**: The Lullabeast build pipeline will write a real `.env` file containing the actual `VITE_OPENROUTER_API_KEY` at project build time. This is separate from the `.env.template` shipped in the repository and is not committed to version control.

### Curated Multimodal Model Shortlist

Fixed dropdown options in the lobby. Each maps to an OpenRouter model ID. All chosen models are selected for low cost and high throughput:

| Display Name | OpenRouter Model ID | Role |
|---|---|---|
| Gemini 3.1 FL | `google/gemini-3.1-flash-lite` | Drawer + Guesser |
| Mistral S4 | `mistralai/mistral-small-2603` | Drawer + Guesser |
| GPT 4o mini | `openai/gpt-4o-mini` | Drawer + Guesser |
| Qwen 3.6 35B | `qwen/qwen3.6-35b-a3b` | Drawer + Guesser |
| Step 3.7 F | `stepfun/step-3.7-flash` | Drawer + Guesser |
| Nova 2 L | `amazon/nova-2-lite-v1` | Drawer + Guesser |

All models must support vision (image input) to function as guessers. All models must support text output to function as drawers. The app does not validate capabilities at runtime; the shortlist is vetted at build time.

### Frontend Stack

- Single-page app: either a single HTML file with inline CSS/JS, or a lightweight React/Vite SPA.
- No backend runtime dependencies.
- No database.
- State management: in-memory React state (or vanilla JS modules) with `localStorage` for settings persistence.
- Canvas rendering (AI drawer): DOM-based SVG composition (not `<canvas>` 2D context) so that elements can be appended incrementally and screenshotted via `XMLSerializer` + `Blob` + `btoa` or `dom-to-image` / `html-to-image` library.
- Canvas rendering (human drawer): HTML5 `<canvas>` element with raster stroke tools.

## Milestones & Timeline

**Milestone 1 — Foundation & Lobby (Days 1–2)**
- Project scaffolding (SPA setup, dark theme baseline).
- `localStorage` settings persistence.
- `.env.template` and build-time env variable support.
- Lobby UI: API key input (with env fallback), rounds/draw-time controls, 4-slot player config with AI/Human toggle and model dropdown, tagline display, 4-player validation.
- Word list data structure and shuffle/deduplication logic.

**Milestone 2 — Game Loop & States (Days 3–4)**
- App state machine: Lobby → Randomizer → Drawing → Round End → Session End.
- Randomizer animation and draw rotation logic.
- Round timer (default 60s), round counter, session progression. Timer yellow warning in final 5s.
- Round End and Session End screens with scoring display.

**Milestone 3 — Canvas & Drawing (Days 5–7)**
- AI drawer: SVG canvas renderer with live append/hot-reload, non-streaming completion calls (one per element), SVG parsing, 500ms cadence, no-text rule detection, 3-strike malformed handling.
- **SVG centering**: System prompt instructs models to center drawings and use large coordinates; container uses `preserveAspectRatio="xMidYMid meet"`.
- Human drawer: HTML5 `<canvas>` drawing tool with pencil, pen, eraser, bucket/flood-fill, color palette (12 preset swatches, no custom hex), line width, undo, clear.
- Screenshot mechanism: DOM SVG → base64 PNG capture for AI canvas; `<canvas>.toDataURL()` for human canvas. Secret word label excluded via DOM scoping.
- No-blank guard: check canvas element node list or pixel data before triggering a guesser screenshot.

**Milestone 4 — Guessing & Scoring (Days 8–9)**
- Guessing agent loop: screenshot every 3s + on new element, debounce 1s, parallel API calls.
- Per-guesser round-level guess history tracking; inject history + "do not repeat" instruction into each prompt.
- Duplicate-guess handling: silent discard for AI, inline warning ("You have already guessed that. Try something else!") for human.
- Correct/incorrect guess detection with accepted lists and fuzzy matching for humans.
- Speech bubble UI (below canvas, non-blocking), guess history sidebar (right of canvas), "got it" badges.
- Human guess input at bottom of sidebar with Enter + submit button.
- Scoring formula implementation and live scoreboard updates.

**Milestone 5 — Integration, Build, End-to-End Test & Fix (Days 10–11)**
- Compile, build, and launch the full application.
- Run one or more complete end-to-end sessions (all-AI and human-in-the-loop configurations).
- For each session, verify:
  - Full game loop completes from Lobby through Session End with correct state transitions.
  - Round timer counts correctly and triggers Round End on expiration.
  - Drawer rotation enforces fairness (each participant draws once before repeats).
  - Scoring math matches the specified formula exactly.
  - No-blank guard prevents guesser API calls against an empty canvas.
  - Screenshots exclude the secret word in both AI and human drawing modes.
  - AI guessers respect the "do not repeat" instruction and guess history.
  - Speech bubbles appear below the canvas without overlapping the drawing area.
  - All 6 curated models can successfully draw and guess when a valid API key is present.
  - API error handling (429 backoff, timeouts, malformed SVG 3-strike, consecutive timeout forfeit) triggers the correct fallback UI states.
- **Fix loop**: If any behavior deviates from the PRD, diagnose the root cause, patch the implementation, and re-run the test. Repeat until all verified behaviors pass.
- Performance sanity check: confirm screenshot generation stays under 500ms per capture on a modern desktop browser.

**Milestone 6 — Final Polish (Day 12)**
- Quality pass across all screens: check typography, spacing, color consistency, animation smoothness at 16:9 (1920×1080).
- Review all UI copy against the Verbatim Strings list for exact character matches.
- Ensure the guess-history scrollbar and human guess input remain usable during rapid guess ingestion.
- Verify participant panel speech-bubble positioning never overlaps the canvas area at any screen width ≥1280px.
- Final screen-recording check: record a full 3-round session at 1920×1080, play back at 720p, and confirm all text is legible and animation timing feels satisfying.
- Apply any finer touches missed in earlier milestones: subtle transitions between states, focus-state polish for keyboard navigation, and edge-case UI graceful degradation (e.g., all agents going "unavailable" simultaneously).

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| OpenRouter API rate limits disrupt game flow | High | Medium | 500ms cadence between drawer calls; 1s debounce on screenshots; 5s backoff on 429; retry up to 3x. If limits persist, extend delays or reduce screenshot frequency dynamically. |
| AI models produce poor-quality SVG drawings | Medium | Medium | Strict system prompt with examples; no-text rule enforcement; drawer points incentivize clarity. Centering instructions in prompt encourage large, visible output. If output is consistently bad, consider adding a "svg examples" few-shot prompt block. |
| Screenshot generation is too slow for real-time feel | Medium | Low | Use `dom-to-image` or native `XMLSerializer` + canvas draw. Target <500ms. If slow, reduce screenshot resolution or frequency. |
| AI guesses are too accurate / too inaccurate, breaking game balance | Medium | Medium | Tweak system prompts (add "you are uncertain" framing for guessers). If models are too good, increase word abstractness or reduce draw time. If too bad, add hint mechanism or loosen accepted list. |
| Browser memory bloat from large SVG + screenshot buffers | Low | Low | Cap max SVG elements per round (~200). Clear canvas between rounds. Use `URL.createObjectURL` for blob cleanup. |
| Human drawing tool feels clunky or hard to use | Low | Medium | Provide familiar tools (pencil, pen, eraser, bucket fill, undo). Rounds are short (15–120s). User can configure an all-AI game if they never want to draw. |
| AI guessers repeat guesses despite history instruction | Low | Medium | Client-side deduplication: maintain per-round guess history per agent. Filter duplicate AI responses silently; show inline warning for human duplicates. |
| All AI guessers fail simultaneously (API outage) | Low | Low | Detect when zero guessers respond for 2 consecutive intervals. End round immediately, award no points, transition cleanly to Round End. |

## Open Questions

1. ~~Should the human drawing tool support touch/stylus input for tablet use, or is mouse-only acceptable given the desktop-first target?~~ **Answered**: Desktop only (mouse input).
2. ~~Should there be a sound effect or visual cue when a guesser gets it right, or is the "got it! 🎯" badge sufficient?~~ **Answered**: Badge only — no sound effects.
3. ~~Do we want a "replay" or "spectate last round" feature, or is the linear session flow sufficient?~~ **Answered**: Linear flow only — no replay.
4. ~~What is the exact lobby tagline copy?~~ **Answered**: "Watch AIs fail at Pictionary... For now."
5. ~~Should the `.env` approach use `VITE_` prefix (Vite convention), or a plain env var name if using a different build tool?~~ **Answered**: Use `VITE_OPENROUTER_API_KEY`.

## Glossary & Domain Terms

- **Drawer**: The participant (AI or human) generating the image for the current round.
- **Guesser**: Any participant not currently drawing, submitting word guesses.
- **Session**: A complete multi-round game from lobby through Session End.
- **Round**: A single draw-and-guess cycle within a session.
- **Slot**: One of the 4 participant positions in the lobby. Each slot is either AI (with a chosen model) or Human.
- **Accepted list**: The array of strings against which guesses are evaluated for correctness, including synonyms and near-matches.
- **humanFuzzy**: Additional strings for misspelling tolerance, applied only to human guesses.
- **No-text rule**: The hard constraint that AI drawer output must not contain `<text>`, `<tspan>`, number-shaped paths, or letter-shaped paths.
- **Screenshot**: A base64-encoded PNG capture of the current canvas, sent to guessing agents as vision input. Must never include the secret word.
- **SVG canvas**: The DOM-based SVG composition area used by AI drawers, where elements are appended incrementally.
- **Human canvas**: The HTML5 `<canvas>` raster drawing surface used by the human drawer.
- **No-blank guard**: The logic that prevents screenshot API calls from being sent to guessers while the canvas has no visible drawing content, saving tokens and avoiding wasted guesses.
- **Guess history**: The per-agent, per-round list of incorrect guesses submitted so far, injected into each guessing prompt to discourage repetition.

## Revision History

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1 | 2026-05-20 | PRD Creator | Initial draft: Problem Statement, Goals, User Stories, Functional Requirements, Edge Cases, Dependencies, Milestones, Risks, Glossary. |
| 0.2 | 2026-05-20 | PRD Creator | Incorporated user annotations: 4-player minimum lobby validation, "Squishy Human" naming, removed pass button, Drawing layout (panels below, history right), human canvas switched to HTML5 raster with bucket tool, 500ms cadence, 3-strike malformed SVG rule, timer yellow warning, secret word hidden for non-drawers, `.env.template` support, env + localStorage API key hybrid, non-blocking speech bubbles below canvas, lobby tagline requirement. |
| 0.3 | 2026-05-20 | PRD Creator | Further corrections: API key display now masked (first 4 + last 4 only); screenshot timing unified to "every 3s OR on new element, whichever is less frequent, min 1s gap"; guess history moved to right sidebar; exactly 4 players required; drawer uses `stream: false` (one non-streaming completion per element); OpenRouter model IDs corrected to exact provider/model format; no-text rule now explicitly requires parsing each SVG chunk for `<text>`/`<tspan>` before appending. |
| 0.4 | 2026-05-20 | PRD Creator | Answered and resolved all Open Questions: desktop-only mouse input for human canvas, no sound effects, no replay/spectate feature, finalized lobby tagline to "Watch AIs fail at Pictionary... For now.", confirmed `VITE_OPENROUTER_API_KEY` env variable naming. Applied tagline to UI/Visual Design and Functional Requirements. |
| 0.5 | 2026-05-27 | PRD Creator | Added explicit build pipeline note in Environment Configuration: the Lullabeast build pipeline writes a real `.env` with `VITE_OPENROUTER_API_KEY` at build time, separate from the repo's `.env.template`. |
| 0.6 | 2026-05-29 | PRD Creator | Added `### Verbatim Strings` subsection under Functional Requirements, capturing all user-facing literal strings, API endpoints, model IDs, env variable names, and `.env.template` content for downstream gate-checking. |
| 0.7 | 2026-05-29 | PRD Creator | Added missing human-variant badge string "You got it! 🎯" to Verbatim Strings list. |
| 0.8 | 2026-06-17 | PRD Creator | Default draw time changed to 60s. Added no-blank canvas guard: AI guessers are not invoked until canvas has visible content. Added per-agent guess history in guessing prompts with "do not repeat" instruction. Added human duplicate-guess warning. Secret word label scoped outside canvas element to ensure screenshots never capture it. Added empty AI guesser response handling edge case and simultaneous AI guesser failure edge case. |
| 0.9 | 2026-06-17 | PRD Creator | Added AI slot auto-naming: when a model is selected, the slot name updates to that model's display name, with a "1 ", "2 ", etc. prefix for duplicates. Replaced human drawer's color picker + custom hex with a fixed 12-preset color palette. |
| 1.0 | 2026-06-17 | PRD Creator | Replaced model list with 6 cheap, high-throughput options: Gemini 3.1 FL, Ministral 3 3b, GPT 4o mini, Qwen 3.6 F, Step 3.7 F, Nova 2 L. Added SVG centering requirement: prompt instructs models to center drawings and use large coordinates; container uses `preserveAspectRatio="xMidYMid meet"`. |
| 1.1 | 2026-06-17 | PRD Creator | Restructured the final phases of the roadmap into two explicit milestones: Milestone 5 (Integration, Build, End-to-End Test & Fix) instructs the build agent to compile, launch, run full E2E sessions, fix any broken behaviors, and re-test until all PRD requirements pass; Milestone 6 (Final Polish) is a dedicated quality pass for refinements and screen-recording validation. |
| 1.2 | 2026-06-23 | Manual edit | Swapped two dropdown models: "Ministral 3 3b" (`mistralai/ministral-3b-2512`) → "Mistral S4" (`mistralai/mistral-small-2603`), and "Qwen 3.6 F" (`qwen/qwen3.6-flash`) → "Qwen 3.6 35B" (`qwen/qwen3.6-35b-a3b`). Updated the Verbatim Strings list and model table to match. |

> ✅ PRD CONVERSION-READY