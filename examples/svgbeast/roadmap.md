# SVG Pictionary Roadmap

- [x] `INFRA-E1` | CRITICAL | Scaffold the zero-backend SPA, dark theme baseline, and build-time env key support

  > Test: Run the dev server, confirm a dark-themed blank app shell loads at 1920×1080 with no console errors, and confirm `.env.template` exists at the project root with the exact placeholder content.

  **Entry Criteria:** Empty project directory, no existing build tooling present.

  **Exit Criteria:** Vite + React (or single-HTML SPA) builds and serves locally, dark high-contrast base theme applied, `.env.template` present at project root, build-time read of `VITE_OPENROUTER_API_KEY` wired into a config module, production build is a self-contained bundle with no backend runtime.

  **TDD Requirements:**
  - `env_config.test.js`: Validates the config module returns the value of `VITE_OPENROUTER_API_KEY` when set and returns empty/undefined (not throwing) when absent.
  - `env_template.test.js`: Asserts `.env.template` exists and contains the exact lines `# OpenRouter API Key (optional — can also be entered in the lobby)` and `VITE_OPENROUTER_API_KEY=your_api_key_here`.

  **Done Criteria:**
  - [ ] `phase/infra-e1` branch contains a buildable SPA scaffold with dark theme baseline
  - [ ] `.env.template` exists at project root with the two exact required lines
  - [ ] Config module reads `VITE_OPENROUTER_API_KEY` at build time
  - [ ] Production build produces a self-contained bundle (no server, no DB)
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A person opening the app sees a clean dark screen instead of a white default page, and a developer cloning the repo finds an `.env.template` file showing where to put their OpenRouter key.
  - **How we'll check:** Run the dev server, open the served URL in a browser sized to 1920×1080, confirm the background is dark with no console errors; then open `.env.template` in the repo root and confirm both required comment and placeholder lines are present verbatim.
  - **If this fails, the user sees:** The app does not open, or it opens as a plain white page, and the project has no `.env.template` showing how to supply an API key.

- [x] `DATA-E1` | HIGH | Build the hardcoded word list and session shuffle/deduplication logic

  > Test: Load the app, programmatically request words until the pool is exhausted, and confirm no word repeats until exhaustion, after which the pool reshuffles and continues without error.

  **Entry Criteria:** `INFRA-E1` complete; config and module structure available.

  **Exit Criteria:** A hardcoded array of 50–60 entries exists, each with `word`, `accepted`, and `humanFuzzy` fields; two-word entries are 20–25% of the list with no three-word entries; a shuffle-on-load function and a session-scoped used-word exclusion set exist; pool resets and reshuffles when exhausted; `New Game` does not clear the used set but a page reload does.

  **TDD Requirements:**
  - `word_list.test.js`: Asserts list length is 50–60, every entry has non-empty `word`/`accepted`/`humanFuzzy`, no entry has three or more words, and two-word entries are within 20–25%.
  - `word_dedup.test.js`: Validates that consecutive draws never repeat a used word until the pool is exhausted, and that exhaustion triggers a reshuffle and continued draws.

  **Done Criteria:**
  - [ ] Word list array meets size, structure, and word-length distribution rules
  - [ ] Shuffle-on-load and used-word exclusion implemented
  - [ ] Pool reset/reshuffle on exhaustion works without interruption
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** Across multiple rounds and `New Game` restarts in one sitting, a viewer never sees the same secret word twice until every word has been used.
  - **How we'll check:** From the word module, draw words repeatedly in a test harness equal to the pool size plus a few more, assert each pre-exhaustion word is unique and that draws continue after exhaustion.
  - **If this fails, the user sees:** The same Pictionary word coming up again and again in the same play session, making the demo look broken or repetitive.

- [x] `CORE-E1` | HIGH | Implement localStorage settings persistence with in-memory fallback

  > Test: Set rounds, draw time, slot configs, and API key; refresh the page; confirm all settings are restored. Then simulate localStorage being unavailable and confirm the app still runs from in-memory state.

  **Entry Criteria:** `INFRA-E1` complete.

  **Exit Criteria:** Rounds, draw time, slot configurations (type, model, names), and the manually entered API key are saved to and restored from `localStorage`; when `localStorage` is cleared or unavailable, the app falls back to in-memory state without crashing.

  **TDD Requirements:**
  - `settings_persistence.test.js`: Validates save/restore round-trip for rounds, draw time, slot configs, and API key via a mocked `localStorage`.
  - `settings_fallback.test.js`: Validates that when `localStorage` throws or is undefined, reads/writes degrade to in-memory state without throwing.

  **Done Criteria:**
  - [ ] Settings persist to and restore from `localStorage`
  - [ ] In-memory fallback works when `localStorage` is unavailable
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** After configuring the game and refreshing the browser, the user finds their rounds, draw time, player slots, and API key still set instead of reset to defaults.
  - **How we'll check:** In a browser, set non-default values, reload the page, and confirm the lobby shows the same values; then disable storage (private mode or mocked throw) and confirm the app still loads.
  - **If this fails, the user sees:** Every page refresh wipes their settings and forces them to re-enter the API key and reconfigure every slot.

- [x] `UI-E1` | CRITICAL | Build the Lobby screen with API key handling, settings controls, 4-slot config, tagline, and validation

  > Test: Open the lobby, confirm the tagline renders, configure 4 slots (mix of AI and Human), select models, observe auto-naming and duplicate counter prefixes, enter an API key, and confirm Start Game enables only when all 4 slots and a valid key are present.

  **Entry Criteria:** `DATA-E1` and `CORE-E1` complete; settings persistence available.

  **Exit Criteria:** Centered dark lobby card renders the exact tagline `Watch AIs fail at Pictionary... For now.`; masked API key input pre-fills from env or `localStorage` and never shows the full key (only first 4 + last 4 with ellipsis when displayed); rounds stepper (1–10, default 3); draw-time slider (15–120s, default 60) with live label; exactly 4 player slots with color avatars, AI/Human toggle, model dropdown (6 fixed options), default names `Agent 1`/`Agent 2`/`Agent 3` and `Squishy Human`; AI slot name auto-updates to model display name with `1 `/`2 `/`3 ` prefix on duplicates; renameable Human slots; `Start Game` CTA disabled until valid key and all 4 slots configured, showing `All 4 slots must be filled.` and an inline API key error when invalid.

  **Entry Criteria:** `DATA-E1` and `CORE-E1` complete.

  **TDD Requirements:**
  - `lobby_validation.test.js`: Validates Start Game is disabled with fewer than 4 configured slots or invalid/missing key and enabled only when both conditions are met; asserts `All 4 slots must be filled.` appears for under-filled config.
  - `lobby_naming.test.js`: Validates AI slot auto-naming to model display name and `1 `/`2 `/`3 ` prefixing for duplicate model selections.
  - `key_masking.test.js`: Validates the key is never rendered in full and displays only first 4 + last 4 characters when shown.

  **Done Criteria:**
  - [ ] Tagline and all default slot names render with exact verbatim strings
  - [ ] Rounds, draw-time, slot type, and model controls work with correct ranges/defaults
  - [ ] AI auto-naming and duplicate counter prefixing work
  - [ ] API key is masked and never displayed in full
  - [ ] Start Game validation enforces 4 slots + valid key with correct messages
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A user sees the "Watch AIs fail at Pictionary... For now." tagline, can pick how many rounds, how long each draw is, set each of 4 players to an AI model or a human, and can only press Start Game once everything is filled in with a key.
  - **How we'll check:** Launch the app, confirm the tagline text, set 3 slots and verify Start Game is disabled with `All 4 slots must be filled.`, fill the 4th and enter a key to confirm it enables, select the same model twice and confirm names become `1 [Name]` / `2 [Name]`, and confirm the key field never shows more than first/last 4 characters.
  - **If this fails, the user sees:** The lobby is missing its tagline or controls, lets them start an unconfigured game that breaks, or exposes their full API key on screen.

- [x] `API-E1` | CRITICAL | Implement the OpenRouter client with auth, completions calls, and error/backoff handling

  > Test: With a valid key, issue a chat completion against a curated model and receive a response; with a simulated 429, confirm the client backs off 5s/10s/15s and retries up to 3 times, then marks the agent failed.

  **Entry Criteria:** `INFRA-E1` complete; API key available from config/lobby.

  **Exit Criteria:** Client posts to `https://openrouter.ai/api/v1` + `/chat/completions` with `Authorization: Bearer ` + key, OpenAI-compatible messages, `stream: false`; supports text prompts and base64 image inputs; 429 triggers backoff 5s→10s→15s with up to 3 retries then marks agent silent; 4xx/5xx logs to console and marks agent failed for the round; per-call 30s timeout supported.

  **TDD Requirements:**
  - `openrouter_request.test.js`: Validates request URL, `Authorization: Bearer ` header, model ID mapping, and `stream: false` payload shape using a mocked fetch.
  - `openrouter_backoff.test.js`: Validates 429 backoff sequence (5s, 10s, 15s), max 3 retries, and final failure marking; validates 4xx/5xx marks agent failed.

  **Done Criteria:**
  - [ ] Requests hit the exact base URL + endpoint with correct auth header and payload
  - [ ] All 6 model IDs map correctly from display names
  - [ ] 429 backoff/retry and 4xx/5xx failure handling implemented
  - [ ] 30s per-call timeout enforced
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** When the key is valid, AI players respond; when OpenRouter is rate-limiting, the game waits and retries rather than crashing, and a stuck agent eventually shows as unavailable instead of freezing the game.
  - **How we'll check:** With a mocked fetch, assert the request shape and headers for one curated model, then drive a 429 response and assert the backoff timings and retry count and the final failed-agent marking.
  - **If this fails, the user sees:** AI players never respond at all, or the whole game hangs indefinitely when OpenRouter is busy.

- [x] `CORE-E2` | CRITICAL | Implement the app state machine across Lobby, Randomizer, Drawing, Round End, and Session End

  > Test: Start a game and step through every state transition (Lobby→Randomizer→Drawing→Round End→[next round Randomizer ...]→Session End) confirming correct ordering and that the final round routes to Session End.

  **Entry Criteria:** `UI-E1` complete; settings and word pool available.

  **Exit Criteria:** A central state machine governs transitions Lobby→Randomizer→Drawing→Round End→Session End; round counter respects configured rounds; final round transitions to Session End instead of another Randomizer; transitions are driven by explicit events (start, drawer-selected, round-ended).

  **TDD Requirements:**
  - `state_machine.test.js`: Validates each legal transition fires correctly, illegal transitions are rejected, and the last round routes to Session End rather than Randomizer.

  **Done Criteria:**
  - [ ] All five states and their legal transitions implemented
  - [ ] Round counter and final-round routing to Session End correct
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** Pressing Start Game moves the user through randomizer, drawing, and round-end screens in order, repeating per round, and finishing on a session-end screen after the last round.
  - **How we'll check:** Drive the state machine in a test for a 3-round config and assert the exact ordered sequence of states ending in Session End.
  - **If this fails, the user sees:** The game gets stuck on a screen, skips a phase, or never reaches the final results screen.

- [x] `UI-E2` | HIGH | Build the Randomizer screen with slot-machine animation and fair draw rotation

  > Test: Trigger the randomizer for several consecutive rounds and confirm each active participant draws exactly once before any repeats, the highlight decelerates onto the winner, and human vs AI post-selection displays differ correctly.

  **Entry Criteria:** `CORE-E2` complete; participant configuration available.

  **Exit Criteria:** 4 compact participant panels with pulsing highlight; highlight cycles fast then decelerates over ~2s and locks on the drawer; per-session draw history ensures each active participant draws once before any second draw, then resets; only active slots eligible; post-selection shows `[Name] is drawing!` (using verbatim `is drawing!`); Human selection reveals `Your word: [WORD]` (verbatim `Your word:`) while AI selection shows only the name + `drawing next` with the word withheld; auto-advances to Drawing after ~2s.

  **TDD Requirements:**
  - `draw_rotation.test.js`: Validates fair rotation — every active participant is selected once before repeats and history resets after a full cycle, with only active slots eligible.
  - `randomizer_display.test.js`: Validates Human selection exposes the word via `Your word:` and AI selection withholds the word and shows `drawing next`.

  **Done Criteria:**
  - [ ] Slot-machine animation decelerates and locks on the selected drawer
  - [ ] Fair rotation enforced with reset after full cycle
  - [ ] Human vs AI post-selection displays correct, with verbatim strings
  - [ ] Auto-advance to Drawing after ~2s
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** At each round start the viewer sees a slot-machine-style highlight spin and stop on one player with "[Name] is drawing!", and over a session every player gets a turn before anyone repeats.
  - **How we'll check:** Run the rotation function across rounds equal to twice the participant count and assert each participant draws once per cycle; in the UI confirm the human sees their word and AI selection hides it.
  - **If this fails, the user sees:** The same player keeps getting picked to draw, or the human is shown drawing without their word (or the AI's secret word leaks on screen).

- [x] `CORE-E3` | HIGH | Implement the round timer, round counter, and session progression with final-5s warning

  > Test: Start a round, watch the timer count down every second from the configured draw time, confirm it turns yellow in the final 5 seconds, and confirm timer expiry triggers Round End.

  **Entry Criteria:** `CORE-E2` complete.

  **Exit Criteria:** Countdown uses the configured draw time (default 60s), updates every second, is always visible during Drawing, turns yellow during the final 5 seconds, and on reaching zero triggers the Round End transition; round counter advances per round.

  **TDD Requirements:**
  - `round_timer.test.js`: Validates countdown decrements per second, yellow-warning flag activates at ≤5s remaining, and zero triggers the round-end event.

  **Done Criteria:**
  - [ ] Timer counts down from configured draw time and updates each second
  - [ ] Yellow warning activates in final 5 seconds
  - [ ] Timer expiry triggers Round End
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** During drawing, the viewer sees a visible countdown that ticks down each second and turns yellow in the last 5 seconds, and the round ends when it hits zero.
  - **How we'll check:** Start a round with a short draw time in a test, advance a fake clock, and assert the per-second decrement, the yellow flag at ≤5s, and the round-end trigger at 0.
  - **If this fails, the user sees:** No visible timer, a timer that never warns or never ends the round, leaving the round running forever.

- [x] `UI-E3` | HIGH | Build the Drawing screen layout with canvas anchor, panels, guess sidebar, input, and timer

  > Test: Enter the Drawing state at 1920×1080 and confirm the canvas dominates the center, participant panels sit below it, the guess history is on the right with the human input fixed at the bottom of that sidebar, and the secret word label sits outside the canvas element DOM.

  **Entry Criteria:** `CORE-E3` complete; state machine routes to Drawing.

  **Exit Criteria:** Canvas occupies majority of width/height as the visual anchor; secret-word indicator is a sibling/descendant of a non-canvas container positioned near the canvas frame (never inside the screenshotted element); compact 4-panel participant strip below the canvas; scrollable guess-history log to the right (newest at bottom); human guess input (text + submit, Enter-to-submit) fixed at the bottom of the sidebar and hidden when the user is the drawer or in all-AI mode; prominent always-visible timer integrated near the canvas frame.

  **TDD Requirements:**
  - `drawing_layout.test.js`: Validates the secret-word label DOM node is not a descendant of the canvas element being screenshotted, panels render below the canvas, and the guess sidebar/input render to the right/bottom.
  - `guess_input_visibility.test.js`: Validates the human guess input is hidden when the user is the drawer or in an all-AI game and visible otherwise.

  **Done Criteria:**
  - [ ] Canvas is the dominant central element at 16:9
  - [ ] Secret-word label lives outside the screenshotted canvas element
  - [ ] Participant panels below, guess sidebar right, input at sidebar bottom
  - [ ] Human input hidden when user is drawer or all-AI game
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** On the main drawing screen the viewer sees a big central canvas with player panels below, a scrolling guess log on the right, a guess box at the bottom of that log, and a clear timer near the canvas.
  - **How we'll check:** Render the Drawing state at 1920×1080, inspect the DOM to confirm the word label is outside the canvas element, confirm panel/sidebar/input placement, and toggle drawer/all-AI states to confirm input visibility.
  - **If this fails, the user sees:** A cramped or mislaid drawing screen where the canvas is small, the guess box is missing, or the secret word sits on the canvas itself.

- [x] `CORE-E4` | CRITICAL | Implement the AI drawer SVG renderer with incremental append, no-text enforcement, and malformed/timeout handling

  > Test: Run an AI drawer round and confirm SVG elements append one at a time at least 500ms apart, the drawing renders centered/scaled via `preserveAspectRatio="xMidYMid meet"`, a `<text>` violation forfeits drawer points, 3 malformed outputs forfeit the round, and 3 consecutive timeouts end the round.

  **Entry Criteria:** `API-E1` and `UI-E3` complete.

  **Exit Criteria:** DOM-based SVG composition area appends one element/group per non-streaming completion call with ≥500ms cadence and immediate re-render; system prompt includes the secret word, the strict no-text rule, SVG-only format, and centering/large-coordinate instructions; container uses `preserveAspectRatio="xMidYMid meet"`; each chunk is parsed into a temp DOM fragment and checked for `<text>`/`<tspan>`/readable characters before append — a violation flags the drawer and forfeits all drawer points for the round; malformed SVG increments a per-agent counter (3 = forfeit, round ends, 0 points, word revealed; <3 = skip and continue); a >30s call skips to next attempt and 3 consecutive timeouts mark the drawer failed and end the round; SVG element count capped (~200).

  **TDD Requirements:**
  - `svg_append.test.js`: Validates elements append incrementally with ≥500ms spacing and that the container renders with `preserveAspectRatio="xMidYMid meet"`.
  - `no_text_rule.test.js`: Validates detection of `<text>`/`<tspan>`/readable-character SVG and that a violation forfeits all drawer points for the round while valid SVG still renders.
  - `malformed_timeout.test.js`: Validates the 3-strike malformed forfeit, skip-on-malformed under 3, the 30s timeout skip, and 3-consecutive-timeout round end.

  **Done Criteria:**
  - [ ] Incremental SVG append at ≥500ms cadence with immediate render
  - [ ] System prompt carries word, no-text rule, SVG-only, and centering instructions
  - [ ] No-text detection and drawer-point forfeit implemented
  - [ ] 3-strike malformed and consecutive-timeout handling implemented
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The viewer watches an AI build a drawing piece by piece, centered and large on the canvas, and if the AI cheats with text or keeps producing garbage the round ends fairly instead of hanging.
  - **How we'll check:** With a mocked OpenRouter returning crafted SVG chunks, assert incremental append timing and centering attribute; feed a `<text>` chunk and assert drawer forfeits points; feed 3 malformed chunks and 3 timeouts and assert the round ends correctly.
  - **If this fails, the user sees:** A tiny or off-screen drawing, an AI that spells the answer in text, or a round that freezes when the AI returns broken output.

- [x] `UI-E4` | MEDIUM | Build the human drawer HTML5 canvas with raster tools and 12-preset palette

  > Test: As the human drawer, draw strokes with pencil/pen, erase, flood-fill an area, change color via the 12 swatches, adjust line width, undo a stroke, and clear the canvas — all rendering in real time on an HTML5 `<canvas>`.

  **Entry Criteria:** `UI-E3` complete; state machine selects a human drawer.

  **Exit Criteria:** When a human slot is the drawer, the canvas switches to an interactive HTML5 `<canvas>` with pencil, pen (smooth stroke), eraser, bucket/flood-fill, a fixed row of exactly 12 preset color swatches (no custom hex, exact default palette colors), line-width selector, undo (last stroke), and clear; mouse input only; raster strokes render in real time; the secret word is shown outside the `<canvas>` element so it is excluded from `canvas.toDataURL()`.

  **TDD Requirements:**
  - `human_tools.test.js`: Validates each tool (pencil, pen, eraser, bucket fill, line width, undo, clear) mutates canvas state as expected and that exactly 12 preset swatches with the specified hex values are present.
  - `human_word_exclusion.test.js`: Validates the secret-word label is not inside the `<canvas>` element and is therefore absent from `canvas.toDataURL()` output.

  **Done Criteria:**
  - [ ] Interactive HTML5 canvas with all required tools works with mouse input
  - [ ] Exactly 12 preset swatches with the specified colors, no custom hex
  - [ ] Undo and clear behave correctly
  - [ ] Secret word excluded from the canvas raster
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** When chosen to draw, the human can sketch with a pencil/pen, erase, fill regions, pick from 12 colors, change thickness, undo, and clear — and their secret word never appears in what the AI guessers see.
  - **How we'll check:** Drive each tool against a test canvas and assert pixel/state changes, assert the swatch set equals the 12 specified colors, and confirm `toDataURL()` output excludes the word label region.
  - **If this fails, the user sees:** Drawing tools that don't work, a missing or wrong color palette, or the secret word leaking into the image the AIs guess from.

- [x] `CORE-E5` | CRITICAL | Implement the screenshot capture pipeline with secret-word exclusion and no-blank guard

  > Test: Trigger a screenshot for an AI drawer (SVG→base64 PNG) and a human drawer (`canvas.toDataURL()`), confirm the PNG never contains the secret word, confirm capture is under 500ms, and confirm a completely blank canvas is skipped (no API call).

  **Entry Criteria:** `CORE-E4` and `UI-E4` complete.

  **Exit Criteria:** SVG canvas subtree is serialized to base64 PNG (via `XMLSerializer`+`Blob`+`btoa` or `dom-to-image`/`html-to-image`) scoped to the canvas element only; human canvas uses `canvas.toDataURL()`; the secret-word label is never captured; a no-blank guard checks for at least one appended SVG element (or non-transparent/non-white pixels for human canvas) and skips the send when empty, re-checking next interval; capture completes <500ms on modern desktop.

  **TDD Requirements:**
  - `screenshot_capture.test.js`: Validates SVG-subtree and human-canvas capture both produce a base64 PNG scoped to the canvas element and excluding the word label node.
  - `no_blank_guard.test.js`: Validates that a blank/empty canvas is detected and the screenshot/API send is skipped, while a canvas with content proceeds.

  **Done Criteria:**
  - [ ] SVG and human-canvas capture both produce scoped base64 PNGs
  - [ ] Secret word never appears in captured output
  - [ ] No-blank guard skips empty canvases and re-checks next interval
  - [ ] Capture stays under 500ms on a modern desktop
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** AI guessers only get sent a picture once there is something drawn, and that picture never contains the answer word.
  - **How we'll check:** Capture both canvas types in a test, assert the output is a base64 PNG without the word-label node, and assert that an empty canvas produces no send while a populated one does.
  - **If this fails, the user sees:** AI players guessing correctly instantly because the word leaked into the image, or wasted guesses against a blank canvas.

- [x] `CORE-E6` | CRITICAL | Implement the guessing loop with screenshot cadence, parallel calls, and per-agent guess history injection

  > Test: Run a round and confirm guessers receive a screenshot every 3s OR on each new element (whichever is less frequent, min 1s gap), all non-drawing agents are called in parallel, and each prompt includes the running guess history plus the do-not-repeat instruction.

  **Entry Criteria:** `CORE-E5` and `API-E1` complete.

  **Exit Criteria:** Screenshots are dispatched every 3s or on each new SVG element, whichever is less frequent, debounced to a minimum 1s gap and gated by the no-blank guard; each non-drawing AI agent is called in parallel via `Promise.all` with a context system prompt (no word/hints); each request includes a running guess-history message containing that agent's prior incorrect guesses this round and the verbatim instruction `Do not repeat any guess you have already submitted this round. Here is your guess history:`; agents return short guess strings.

  **TDD Requirements:**
  - `guess_cadence.test.js`: Validates the "every 3s OR on new element, whichever less frequent, min 1s gap" scheduling and that the no-blank guard gates sends.
  - `guess_history_prompt.test.js`: Validates each guesser prompt includes the agent's prior incorrect guesses and the exact do-not-repeat instruction string, and that calls fire in parallel.

  **Done Criteria:**
  - [ ] Screenshot cadence and 1s debounce implemented correctly
  - [ ] Non-drawing agents called in parallel
  - [ ] Per-agent running guess history + verbatim do-not-repeat instruction injected
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** While an image is being drawn, the AI players periodically offer guesses, and they avoid repeating their own earlier wrong guesses.
  - **How we'll check:** With a mocked clock and fetch, advance time and add elements to assert the dispatch cadence and debounce, and inspect the outgoing prompts to confirm the guess-history list and exact instruction string are present.
  - **If this fails, the user sees:** AI players that never guess, guess too rapidly, or keep repeating the same wrong word over and over.

- [x] `CORE-E7` | HIGH | Implement correct/incorrect guess detection, scoring, and duplicate handling

  > Test: Submit AI guesses (exact and accepted-list matches, no fuzzy) and human guesses (with humanFuzzy tolerance), confirm correct matches award decaying points and +40 drawer points, confirm duplicate AI guesses are silently discarded, and confirm round-end triggers fire.

  **Entry Criteria:** `CORE-E6` complete; `DATA-E1` accepted/humanFuzzy lists available.

  **Exit Criteria:** AI guesses match against the word's `accepted` list with no fuzzy tolerance; human guesses additionally use `humanFuzzy` tolerance; correct guesses award `Math.max(10, Math.round(200 - (elapsed/drawTime)*190))` with ties allowed and award the drawer +40 per correct guesser; no first-guess bonus; the actual correct guess text is never shown publicly; AI duplicate guesses are silently treated as incorrect (no bubble/log, no deduction); empty/whitespace AI guesses are logged and skipped; round ends when the timer expires or all guessers answer correctly; if all guessers fail, drawer gets 0 drawer points.

  **TDD Requirements:**
  - `guess_detection.test.js`: Validates AI exact/accepted matching without fuzziness and human matching with `humanFuzzy`, and that correct text is not exposed.
  - `scoring_formula.test.js`: Validates the exact decay formula at several elapsed values, the 10-point floor, the 200 max at t=0, ties, and the +40 drawer award per correct guesser.
  - `dedup_empty_guess.test.js`: Validates silent discard of duplicate AI guesses and skip of empty/whitespace guesses.

  **Done Criteria:**
  - [ ] Accepted-list matching for AI and humanFuzzy matching for humans
  - [ ] Scoring formula, floor/max, ties, and drawer +40 implemented exactly
  - [ ] Correct guess text never shown publicly
  - [ ] Duplicate AI and empty guesses handled silently
  - [ ] Round-end triggers (timer / all-correct / all-fail) correct
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** When a player guesses right they earn points that are higher the earlier they guess, the drawer earns points for each correct guesser, and the exact winning word is never spoiled in the log.
  - **How we'll check:** Feed known guesses against a known word in tests and assert correct/incorrect classification per participant type, assert scores match the formula at several timestamps, and assert duplicates/empties are ignored.
  - **If this fails, the user sees:** Wrong scores, the answer being spoiled before the reveal, or repeated/empty guesses cluttering the game.

- [x] `UI-E5` | HIGH | Build guess UI: speech bubbles, got-it badges, guess-history sidebar, and human duplicate warning

  > Test: During a round, confirm AI incorrect guesses pop as non-overlapping speech bubbles below the canvas that auto-dismiss after ~2s then land in the right sidebar, correct guesses show the got-it badge (no text), and a human duplicate guess shows the inline warning without sending.

  **Entry Criteria:** `CORE-E7` and `UI-E3` complete.

  **Exit Criteria:** AI incorrect guesses appear as colored pop-animated speech bubbles near the agent panel below the canvas (never overlapping the drawing area), auto-dismiss after ~2s, then appear in the right guess-history sidebar; human incorrect guesses go straight to the sidebar (no bubble); correct guesses show `[Name] got it! 🎯` (verbatim ` got it! 🎯`) or `You got it! 🎯` plus a panel badge and timestamp, with no guess text and no sound; agent panels show `thinking...` while awaiting a response and `unavailable` after repeated failures; human duplicate guesses show the inline warning `You have already guessed that. Try something else!` and are not sent.

  **TDD Requirements:**
  - `speech_bubble.test.js`: Validates AI incorrect guesses render as bubbles below the canvas (non-overlapping), auto-dismiss after ~2s, then appear in the sidebar; human incorrect guesses skip the bubble.
  - `badge_and_status.test.js`: Validates correct-guess badges use the exact `got it! 🎯` strings without showing guess text, and panel status uses `thinking...` / `unavailable`.
  - `human_dup_warning.test.js`: Validates a duplicate human guess shows `You have already guessed that. Try something else!` and is not dispatched.

  **Done Criteria:**
  - [ ] AI bubbles render below canvas without overlap, auto-dismiss, then log
  - [ ] Correct badges and panel status use exact verbatim strings
  - [ ] Human duplicate warning shown and duplicate not sent
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** Viewers see AI wrong guesses pop up briefly as colored bubbles that don't cover the drawing and then collect in the side log, correct guessers get a "got it! 🎯" badge, and a human who repeats a guess gets a gentle nudge.
  - **How we'll check:** Trigger AI incorrect/correct and human duplicate guesses in the rendered Drawing screen and assert bubble placement/dismissal, sidebar logging, exact badge/status strings, and the duplicate warning with no send.
  - **If this fails, the user sees:** Guess bubbles covering the drawing, missing or wrongly-worded badges, or the human able to spam duplicate guesses.

- [x] `UI-E6` | MEDIUM | Build the Round End screen with word reveal, ranked scoreboard, and skippable auto-advance

  > Test: At round end, confirm the screen shows `The word was: [WORD]` with an entrance animation, a 1st–4th ranked scoreboard with totals and this-round deltas, and a 5s countdown to the next round (or Session End) that can be clicked to skip.

  **Entry Criteria:** `CORE-E7` and `CORE-E2` complete.

  **Exit Criteria:** Centered layout reveals `The word was: [WORD]` (verbatim `The word was:`) with a brief scale/fade entrance; ranked scoreboard shows 1st–4th names, total scores, and per-round deltas; a 5s countdown auto-advances to the next round or Session End on the final round, and clicking skips it.

  **TDD Requirements:**
  - `round_end.test.js`: Validates the word reveal uses the verbatim prefix, the scoreboard ranks correctly with totals and deltas, and the 5s countdown advances (final round → Session End) with click-to-skip.

  **Done Criteria:**
  - [ ] Word reveal with verbatim prefix and entrance animation
  - [ ] Ranked 1st–4th scoreboard with totals and deltas
  - [ ] Skippable 5s auto-advance to next round / Session End
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** After each round the viewer sees the revealed word, a ranked scoreboard showing who earned what this round, and a short countdown to the next round they can skip.
  - **How we'll check:** Enter Round End in a test with known scores, assert the reveal text and ranked totals/deltas, advance the countdown clock and assert auto-advance plus click-to-skip behavior.
  - **If this fails, the user sees:** No word reveal, a wrong or missing scoreboard, or a round-end screen that never advances.

- [x] `UI-E7` | MEDIUM | Build the Session End screen with podium, stat callouts, and New Game

  > Test: At session end, confirm the 2nd/1st/3rd podium layout with avatars/names/final scores, the four stat callouts (fastest guess, best drawer, most rounds won, average guess time), and that New Game returns to the Lobby preserving settings without resetting the used-word pool.

  **Entry Criteria:** `UI-E6` and `CORE-E2` complete.

  **Exit Criteria:** Podium shows 2nd left, 1st center (elevated), 3rd right with avatar/name/final score; four compact stat callouts below (fastest guess, best drawer, most rounds won, average guess time); `New Game` (verbatim) returns to Lobby preserving rounds, draw time, slot configs, and API key, and does NOT reset the used-word pool.

  **TDD Requirements:**
  - `session_end.test.js`: Validates podium ordering by final score, correct computation of all four stat callouts, and that New Game preserves settings while leaving the used-word pool intact.

  **Done Criteria:**
  - [ ] Podium ordering and final scores correct
  - [ ] All four stat callouts computed correctly
  - [ ] New Game preserves settings and keeps used-word pool
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** At the end the viewer sees a winners' podium, a few fun stats about the session, and a New Game button that restarts with the same settings but doesn't recycle already-used words.
  - **How we'll check:** Enter Session End in a test with known per-round data, assert podium order and the four computed stats, click New Game and assert settings are retained while the used-word set is unchanged.
  - **If this fails, the user sees:** A wrong winner on the podium, incorrect stats, or New Game wiping their settings or repeating old words.

- [x] `TEST-E1` | CRITICAL | Integrate, build, and run end-to-end sessions with a fix loop until all PRD behaviors pass

  > Test: Build and launch the full app, run complete all-AI and human-in-the-loop sessions, and verify the full game loop, timer, rotation fairness, scoring math, no-blank guard, word exclusion, do-not-repeat, non-overlapping bubbles, all 6 models participating, error fallbacks, and sub-500ms screenshots — fixing and re-running until all pass.

  **Entry Criteria:** All `CORE`, `UI`, `API`, and `DATA` phases complete.

  **Exit Criteria:** The app compiles, builds, and launches; one or more full sessions (all-AI and human-in-the-loop) complete Lobby→Session End with correct transitions; timer, rotation fairness, exact scoring math, no-blank guard, secret-word exclusion (both modes), AI do-not-repeat adherence, non-overlapping speech bubbles, all 6 curated models drawing/guessing with a valid key, and all error fallbacks (429 backoff, timeout, malformed 3-strike, consecutive-timeout forfeit) are verified; screenshot generation stays <500ms; any deviation is root-caused, patched, and re-tested until all listed behaviors pass.

  **TDD Requirements:**
  - `e2e_all_ai.test.js`: Drives a full all-AI session with mocked OpenRouter and asserts the complete state flow, scoring totals, and clean Session End.
  - `e2e_human_loop.test.js`: Drives a human-in-the-loop session asserting human draw/guess handling, word exclusion, and rotation fairness.
  - `e2e_error_paths.test.js`: Validates 429 backoff, 30s timeout skip, consecutive-timeout forfeit, and malformed 3-strike fallbacks surface the correct UI states.

  **Done Criteria:**
  - [ ] Full all-AI and human-in-the-loop sessions complete end-to-end
  - [ ] Scoring, rotation, timer, no-blank guard, and word exclusion verified
  - [ ] All 6 models participate; all error fallbacks trigger correct UI
  - [ ] Screenshot generation confirmed <500ms
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A user can run a full game from lobby to final podium without manual intervention in both watch-only and play-along modes, and it behaves correctly even when the API rate-limits or an AI misbehaves.
  - **How we'll check:** Build and launch the app, run scripted full sessions (all-AI and human) against a mocked/keyed OpenRouter, and assert the complete flow, scoring, fairness, guard, word exclusion, model participation, and error-fallback behaviors, re-running after each fix.
  - **If this fails, the user sees:** A game that crashes, stalls, miscounts scores, leaks the word, or breaks when the API has trouble — unusable for recording.

- [x] `UI-E8` | LOW | Final polish pass on typography, animation, accessibility, and 1080p screen-recording legibility

  > Test: Record a full 3-round session at 1920×1080, play it back at 720p, and confirm all UI text is legible, animations feel smooth, speech bubbles never overlap the canvas at widths ≥1280px, focus states are visible, and all UI copy matches the Verbatim Strings list exactly.

  **Entry Criteria:** `TEST-E1` complete; all screens functional.

  **Exit Criteria:** Typography, spacing, and color consistency reviewed at 16:9; all UI copy matches the Verbatim Strings list character-for-character; guess-history scrollbar and human input remain usable under rapid guess ingestion; speech bubbles never overlap the canvas at any width ≥1280px; visible focus states and WCAG AA contrast on dark backgrounds; a full 3-round recording at 1920×1080 plays back legibly at 720p; subtle state transitions and edge-case graceful degradation (e.g., all agents unavailable) applied.

  **TDD Requirements:**
  - `verbatim_strings.test.js`: Asserts every string in the PRD Verbatim Strings list appears character-for-character somewhere in the built app/config.
  - `accessibility_contrast.test.js`: Validates interactive elements expose visible focus states and text/background pairs meet WCAG AA contrast on the dark theme.

  **Done Criteria:**
  - [ ] All verbatim strings present character-for-character
  - [ ] Focus states visible and contrast meets WCAG AA
  - [ ] Bubbles never overlap canvas at ≥1280px; sidebar/input usable under load
  - [ ] 1080p recording is legible at 720p playback
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** A 30-second clip recorded at 1080p and watched at 720p reads clearly as "AI models competing in real time," with crisp text, smooth animation, and no overlapping elements.
  - **How we'll check:** Audit each screen against the Verbatim Strings list and contrast/focus requirements, then record and play back a full session at the target resolutions to confirm legibility and bubble placement.
  - **If this fails, the user sees:** Blurry or cut-off text, mismatched labels, overlapping bubbles, or footage that isn't clean enough to share without editing.