# GridBeast Roadmap

- [x] `INFRA-E1` | CRITICAL | Initialize React + Vite project scaffold producing a static `dist/` build

  > Test: Run `npm install && npm run build`, confirm a `dist/` folder is produced, then run `npm run dev` and load the served URL to see a blank app shell render without console errors.

  **Entry Criteria:** Empty repository, no `package.json` present, Node.js available on host.

  **Exit Criteria:** `package.json` declares React + Vite; `npm run dev` serves the app on localhost; `npm run build` emits a self-contained static `dist/` folder; no backend, API, or auth code exists; work committed on `phase/infra-e1`.

  **TDD Requirements:**
  - `build.test.ts`: Asserts `npm run build` exits 0 and `dist/index.html` plus a bundled JS asset exist.
  - `scaffold.test.ts`: Asserts the root React component mounts into the DOM without throwing.

  **Done Criteria:**
  - [ ] `npm run dev` serves an app shell with no console errors
  - [ ] `npm run build` produces a static `dist/` folder with no secrets
  - [ ] No server/API/auth dependencies are present in `package.json`
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can open the app in a browser and see it load instantly with nothing to install or sign into.
  - **How we'll check:** Run `npm run build` (expect exit 0 and a `dist/` folder), then `npm run dev` and open the printed localhost URL; confirm the page loads with an empty app shell and a clean browser console.
  - **If this fails, the user sees:** The page does not load, or the browser shows a blank screen with an error instead of the app.

- [x] `UI-E1` | HIGH | Render a labeled 10×20 grid with click selection and a formula bar showing raw cell content

  > Test: Load the app, confirm columns A–J and rows 1–20 are labeled, click cell B3, confirm it becomes the selected/highlighted cell and the formula bar reflects its raw content (empty initially).

  **Entry Criteria:** `INFRA-E1` complete; React app shell mounts; `dist/` build succeeds.

  **Exit Criteria:** A fixed grid renders with letter-labeled columns A–J and number-labeled rows 1–20; clicking any cell selects it and visibly highlights it; a formula bar component displays the selected cell's raw content (literal text or `=formula`), not a computed value; work committed on `phase/ui-e1`.

  **TDD Requirements:**
  - `grid-render.test.tsx`: Asserts 10 column labels (A–J), 20 row labels (1–20), and 200 cell elements render.
  - `selection.test.tsx`: Asserts clicking a cell sets it as selected and the formula bar shows that cell's raw content.

  **Done Criteria:**
  - [ ] Grid shows columns A–J and rows 1–20 with visible labels
  - [ ] Clicking a cell selects and highlights it
  - [ ] Formula bar shows the selected cell's raw content (not computed value)
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user sees a real-looking spreadsheet grid with lettered columns and numbered rows, and clicking any cell selects it and shows its contents in a formula bar at the top. (US-2, FR-1, FR-2, G5)
  - **How we'll check:** Launch the dev server, confirm column headers A–J and row headers 1–20 are visible, click cell B3, and confirm B3 highlights and the formula bar reflects its raw content.
  - **If this fails, the user sees:** The grid is missing row/column labels, or clicking a cell does nothing and no formula bar appears — it does not look like a spreadsheet.

- [x] `DATA-E1` | HIGH | In-memory cell store with literal entry (number vs text), edit/commit, and display of literal values

  > Test: Click A1, type `42`, press Enter — A1 displays `42`; click A2, type `Price`, press Enter — A2 displays `Price` verbatim; reselect A1 and confirm the formula bar shows `42`.

  **Entry Criteria:** `UI-E1` complete; grid renders and cell selection works; formula bar shows raw content.

  **Exit Criteria:** A plain in-memory reactive store holds per-cell raw source and computed display; editing is possible both in-cell and in the formula bar, committing on Enter or blur; content not starting with `=` is stored as a number when it parses as one, otherwise as text; text literals display verbatim and are never treated as formulas/references; no persistence (state clears on refresh); work committed on `phase/data-e1`.

  **TDD Requirements:**
  - `literal-parse.test.ts`: Asserts `42` and `0.5` store as numbers, `Price` and `1a` store as text.
  - `commit.test.tsx`: Asserts editing via formula bar and in-cell both commit on Enter and on blur, updating the store.
  - `no-persistence.test.ts`: Asserts a fresh store starts empty (no localStorage read).

  **Done Criteria:**
  - [ ] Numeric literals stored as numbers; non-numeric literals stored as text
  - [ ] Text literals display verbatim and never error
  - [ ] Edits commit on Enter and on blur from both in-cell and formula-bar entry
  - [ ] Grid state is in-memory only and clears on page refresh
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can type a number or a word into any cell and see it stay there, with numbers and text both displayed correctly and the formula bar showing exactly what was typed. (US-2, FR-2, FR-3, EC-10)
  - **How we'll check:** In the dev server, type `42` into A1 (expect cell shows `42`), type `Price` into A2 (expect cell shows `Price` with no error), reselect A1 and confirm the formula bar shows `42`.
  - **If this fails, the user sees:** Typed values disappear, a word like `Price` shows an error instead of the text, or the formula bar shows the wrong content.

- [x] `CORE-E1` | CRITICAL | Tokenizer for `=` formulas (numbers, cell refs, operators, parens, range colon, function names)

  > Test: Run the tokenizer unit suite; tokenizing `=A1+B1*2` yields the ordered token stream `[ref A1, +, ref B1, *, num 2]` and `=SUM(A1:A3)` yields `[func SUM, (, ref A1, :, ref A3, )]`.

  **Entry Criteria:** `DATA-E1` complete; cell store exists; formula text (content starting with `=`) is identifiable.

  **Exit Criteria:** A standalone tokenizer module converts a formula string (without the leading `=`) into a token stream covering number literals, cell references (`A1`, `B12`), operators `+ - * /`, parentheses, the range colon `:`, and function-name identifiers; unrecognized characters produce a tokenizer error signal (no exception thrown); `eval()`/`new Function()` are not used; work committed on `phase/core-e1`.

  **TDD Requirements:**
  - `tokenizer.test.ts`: Asserts correct token streams for `A1+B1*2`, `(A1+B1)*2`, `-A1`, `3*-2`, `SUM(A1:A3)`, and `0.1+0.2`.
  - `tokenizer-errors.test.ts`: Asserts unrecognized input (e.g. `A1 @ B1`) returns an error signal rather than throwing.

  **Done Criteria:**
  - [ ] Tokenizer emits correct tokens for arithmetic, references, parens, ranges, and functions
  - [ ] Unary minus context is tokenizable (e.g. `-A1`, `3*-2`)
  - [ ] Invalid characters yield an error signal, never an exception
  - [ ] No use of `eval()` or `new Function()`
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** This is an internal building block; users do not interact with it directly, but it is what lets a typed formula begin to be understood instead of guessed at. (NFR-3, FR-4, FR-5)
  - **How we'll check:** Run the tokenizer unit suite from the project root and confirm the token streams for `A1+B1*2`, `SUM(A1:A3)`, and `3*-2` match the expected ordered tokens, and that `A1 @ B1` returns an error signal.
  - **If this fails, the user sees:** Formulas later misbehave or show parse errors for input that should be valid — surfaced in downstream phases.

- [x] `CORE-E2` | CRITICAL | Parser building an AST with correct precedence, parentheses, unary minus, and left-associativity

  > Test: Run the parser unit suite; parsing `A1+B1*2` yields an AST where `*` is evaluated before `+`, `(A1+B1)*2` groups the addition first, and malformed inputs (`A1+`, `*3`, `()`, unbalanced parens) return a parse-error result.

  **Entry Criteria:** `CORE-E1` complete; tokenizer emits token streams.

  **Exit Criteria:** A parser (recursive-descent or shunting-yard → AST/RPN, never `eval`) consumes tokens and produces an explicit AST/RPN; `* /` bind tighter than `+ -`; parentheses override precedence with arbitrary nesting; operators are left-associative; unary minus is supported (`-A1`, `3*-2`); malformed formulas return a structured parse-error result (no throw, no silently-wrong tree); work committed on `phase/core-e2`.

  **TDD Requirements:**
  - `parser-precedence.test.ts`: Asserts `A1+B1*2` multiplies before adding and `(A1+B1)*2` adds before multiplying.
  - `parser-unary.test.ts`: Asserts `-A1` and `3*-2` parse as unary negation.
  - `parser-assoc.test.ts`: Asserts `10-2-3` is left-associative (groups as `(10-2)-3`).
  - `parser-malformed.test.ts`: Asserts `A1+`, `*3`, `()`, and `((A1)` each return a parse-error result without throwing.

  **Done Criteria:**
  - [ ] `* /` bind tighter than `+ -`; parentheses override; arbitrary nesting supported
  - [ ] Operators are left-associative
  - [ ] Unary minus is supported
  - [ ] Malformed formulas return a parse-error result, never an exception
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** This internal building block is what guarantees that math entered into the grid follows the same rules a person expects on paper — multiplication before addition, parentheses first. (G1, FR-5, FR-13, EC-1, EC-2, EC-13)
  - **How we'll check:** Run the parser unit suite from the project root and confirm `A1+B1*2` produces a tree that multiplies first, `(A1+B1)*2` adds first, and `A1+` / `*3` / `()` return parse-error results.
  - **If this fails, the user sees:** Formulas compute wrong answers (e.g. `8` shown as `10`) or broken formulas crash instead of showing an error — surfaced in downstream phases.

- [x] `CORE-E3` | CRITICAL | Evaluator: arithmetic, cell-reference resolution, empty-cell=0, error tokens, and error propagation

  > Test: With A1=2 and B1=3, evaluating `=A1+B1*2` returns 8; `=(A1+B1)*2` returns 10; `=5/0` returns `#DIV/0!`; `=Z99` on a 10×20 grid returns `#REF!`; `=<a #DIV/0! cell>+1` returns an error token, not `NaN`.

  **Entry Criteria:** `CORE-E2` complete; parser produces an AST/RPN; cell store exposes computed values for references.

  **Exit Criteria:** An evaluator walks the AST/RPN to produce a numeric result or an error token; cell references resolve to the referenced cell's computed value; references to empty cells evaluate to 0 in arithmetic context; the defined error set is implemented — `#DIV/0!`, `#REF!` (out-of-grid reference), `#NAME?`/`#ERROR!` (unparseable formula); a formula referencing an errored cell yields an error (never `NaN`/`undefined`/crash); work committed on `phase/core-e3`. (Cycle handling is deferred to `CORE-E5`.)

  **TDD Requirements:**
  - `eval-arithmetic.test.ts`: Asserts `A1+B1*2`→8 and `(A1+B1)*2`→10 with A1=2,B1=3.
  - `eval-refs.test.ts`: Asserts a reference resolves to the target's computed value and an empty-cell reference evaluates to 0.
  - `eval-errors.test.ts`: Asserts `5/0`→`#DIV/0!`, `Z99`→`#REF!`, and an unparseable formula→`#NAME?`/`#ERROR!`.
  - `eval-propagation.test.ts`: Asserts a formula referencing a `#DIV/0!` cell yields an error token, not `NaN`.

  **Done Criteria:**
  - [ ] Arithmetic with refs computes correct numeric results
  - [ ] Empty-cell references evaluate to 0
  - [ ] `#DIV/0!`, `#REF!`, `#NAME?`/`#ERROR!` are produced for their respective conditions
  - [ ] Errors propagate as error tokens, never `NaN`/`undefined`/crash
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can type a formula like `=A1+B1*2` and see the correct numeric answer, and when they make a mistake like dividing by zero or pointing at a cell off the grid they see a clear error label instead of a crash. (G1, G3, US-1, FR-6, FR-9, FR-10, EC-1, EC-6, EC-7, EC-11, EC-12)
  - **How we'll check:** Run the evaluator unit suite from the project root and confirm `=A1+B1*2`→8, `=(A1+B1)*2`→10, `=5/0`→`#DIV/0!`, `=Z99`→`#REF!`, and a formula referencing a `#DIV/0!` cell returns an error token.
  - **If this fails, the user sees:** A cell shows the wrong number, or shows `NaN`/`undefined`/`Infinity`, or the page crashes instead of showing a tidy error like `#DIV/0!`.

- [x] `CORE-E4` | CRITICAL | Dependency graph and topological-order recalculation on cell edit

  > Test: Set C5=`=C6+1` then C6=4 — C5 shows 5 (dependency below dependent resolves correctly); set A1=2, B1=`=A1*2`, C1=`=B1+1`, then change A1 to 5 — B1 becomes 10 and C1 becomes 11 in one action.

  **Entry Criteria:** `CORE-E3` complete; evaluator computes single-cell results and references resolve to computed values.

  **Exit Criteria:** Editing a cell rebuilds/updates a dependency graph from cell references and recalculates every directly and transitively dependent cell; evaluation order is derived from the dependency graph (topological), never from physical grid/row order; a dependency placed physically below or after its dependent still resolves correctly; recalc completes without perceptible delay; work committed on `phase/core-e4`.

  **TDD Requirements:**
  - `depgraph.test.ts`: Asserts the graph records edges for `=A1*2` (B1→A1) and produces a topological order.
  - `recalc-transitive.test.ts`: Asserts changing A1 updates both B1 (`=A1*2`) and C1 (`=B1+1`) in one edit.
  - `recalc-order-independent.test.ts`: Asserts C5=`=C6+1` with C6=4 yields C5=5 regardless of physical position.

  **Done Criteria:**
  - [ ] Editing a cell recalculates all direct and transitive dependents
  - [ ] Recalc order is topological, not grid/row order
  - [ ] A dependency below/after its dependent resolves correctly
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can change one number and watch every cell that depends on it — directly or down a chain — update instantly and correctly, no matter where those cells sit on the grid. (G2, US-3, FR-7, NFR-4, EC-3, EC-4)
  - **How we'll check:** In the dev server, set A1=2, B1=`=A1*2` (shows 4), C1=`=B1+1` (shows 5), change A1 to 5, and confirm B1→10 and C1→11; separately set C5=`=C6+1` then C6=4 and confirm C5 shows 5.
  - **If this fails, the user sees:** Changing a number leaves dependent cells showing stale values, or a formula that points at a cell lower on the grid shows the wrong answer.

- [x] `CORE-E5` | CRITICAL | Cycle detection emitting `#CIRC!` in all involved cells without hanging

  > Test: Set A1=`=B1` and B1=`=A1` — both show `#CIRC!` and other cells remain editable; set A1=`=B1`, B1=`=C1`, C1=`=A1` — all three show `#CIRC!`; set A1=`=A1` — A1 shows `#CIRC!`; the tab never freezes.

  **Entry Criteria:** `CORE-E4` complete; dependency graph and topological recalc exist.

  **Exit Criteria:** Cycle detection (graph-coloring white/gray/black or visited-set DFS) identifies circular references — including self-reference and multi-node cycles — and writes `#CIRC!` into every cell involved in the cycle; the engine never infinite-loops, hangs, or freezes the tab; cells outside the cycle remain editable and recalculable; work committed on `phase/core-e5`.

  **TDD Requirements:**
  - `cycle-detect.test.ts`: Asserts two-node (`A1=B1`, `B1=A1`) and three-node (`A1=B1`, `B1=C1`, `C1=A1`) cycles are detected.
  - `cycle-self.test.ts`: Asserts a self-reference (`A1=A1`) is detected.
  - `cycle-token.test.ts`: Asserts `#CIRC!` is written into all cells involved in the cycle.
  - `cycle-no-hang.test.ts`: Asserts recalculation terminates (bounded) in the presence of a cycle.

  **Done Criteria:**
  - [ ] Two-node, three-node, and self-reference cycles are detected
  - [ ] `#CIRC!` appears in every cell involved in a cycle
  - [ ] Recalculation always terminates; the tab never hangs or freezes
  - [ ] Cells outside the cycle remain editable
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can deliberately create a circular reference and see every cell in the loop show `#CIRC!` while the app stays fully responsive and other cells stay editable. (G3, US-4, FR-8, EC-5, EC-14, EC-15)
  - **How we'll check:** In the dev server, set A1=`=B1` and B1=`=A1` (expect both show `#CIRC!`), then set A1=`=B1`,B1=`=C1`,C1=`=A1` (expect all three show `#CIRC!`), then A1=`=A1` (expect `#CIRC!`); after each, edit an unrelated cell to confirm the page is still responsive.
  - **If this fails, the user sees:** The page freezes or becomes unresponsive when a loop is created, or the looping cells show a wrong number instead of `#CIRC!`.

- [x] `CORE-E6` | HIGH | Aggregate functions `SUM`, `AVERAGE`, `MIN`, `MAX` over contiguous ranges with range-member recalc

  > Test: A1:A3 = 10,20,30; `=SUM(A1:A3)`→60, change A2 to 25 →65; `=AVERAGE(A1:A3)`→20 then 21.67; with A1=10,A2=`hello`,A3=20: `=SUM`→30, `=AVERAGE`→15, `=MAX`→20; `=MIN`/`=MAX` over a range with no numeric values →`#REF!`.

  **Entry Criteria:** `CORE-E3`, `CORE-E4`, and `CORE-E5` complete; evaluator, dependency tracking, and ranges tokenize/parse.

  **Exit Criteria:** `SUM`, `AVERAGE`, `MIN`, `MAX` aggregate a contiguous rectangular range (`A1:A3`); empty cells contribute 0 to `SUM` and are ignored by `AVERAGE`/`MIN`/`MAX`; text cells are ignored by all four; `MIN`/`MAX` return `#REF!` when the range contains no valid numeric values; each aggregate is registered as a dependent of every range member so changing any member triggers recalc; work committed on `phase/core-e6`.

  **TDD Requirements:**
  - `agg-sum-avg.test.ts`: Asserts `SUM(A1:A3)`=60 then 65 after edit; `AVERAGE`=20 then 21.67.
  - `agg-minmax.test.ts`: Asserts `MIN`=10, `MAX`=30 over 10,20,30.
  - `agg-mixed.test.ts`: Asserts text/empty cells are ignored (SUM→30, AVERAGE→15, MAX→20 for 10,`hello`,20).
  - `agg-empty-range.test.ts`: Asserts `MIN`/`MAX` over a non-numeric range return `#REF!`.
  - `agg-recalc.test.ts`: Asserts editing any range member recalculates the aggregate.

  **Done Criteria:**
  - [ ] `SUM`, `AVERAGE`, `MIN`, `MAX` compute correctly over contiguous ranges
  - [ ] Empty cells contribute 0 to `SUM`, ignored by `AVERAGE`/`MIN`/`MAX`; text ignored by all
  - [ ] `MIN`/`MAX` return `#REF!` over a range with no numeric values
  - [ ] Editing any range member recalculates the aggregate
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can total or summarize a block of cells with `=SUM(A1:A3)`, `=AVERAGE(...)`, `=MIN(...)`, or `=MAX(...)`, and the result updates the moment any cell in that block changes. (FR-11, EC-8, EC-16)
  - **How we'll check:** In the dev server, set A1:A3=10,20,30 and E1=`=SUM(A1:A3)` (expect 60), change A2 to 25 (expect 65); set A2=`hello` and confirm `=SUM(A1:A3)`→30 and `=AVERAGE(A1:A3)`→15.
  - **If this fails, the user sees:** A range total shows the wrong number, fails to update when a cell in the range changes, or errors on a range that mixes numbers and text.

- [x] `UI-E2` | HIGH | Wire the engine into the grid: render computed values in cells and trim floating-point display noise

  > Test: Enter A1=2, B1=3, C1=`=A1+B1*2` — C1 cell shows 8 while selecting C1 shows `=A1+B1*2` in the formula bar; enter `=0.1+0.2` — the cell shows `0.3`, not `0.30000000000000004`.

  **Entry Criteria:** `CORE-E6` complete; the engine produces computed values and error tokens for the cell store; `DATA-E1` display layer renders cell content.

  **Exit Criteria:** Each cell displays its computed value (number or error token) while the formula bar shows the selected cell's raw source; numeric display is trimmed to avoid floating-point tails (e.g. `parseFloat(result.toFixed(10))`) while internal numeric accuracy is preserved; error tokens render verbatim in-cell; work committed on `phase/ui-e2`.

  **TDD Requirements:**
  - `display-computed.test.tsx`: Asserts a formula cell shows its computed value while its formula bar shows the raw source.
  - `display-float.test.ts`: Asserts `0.1+0.2` displays as `0.3` and other long tails are trimmed.
  - `display-error.test.tsx`: Asserts an errored cell renders its token (e.g. `#DIV/0!`) in-cell.

  **Done Criteria:**
  - [ ] Cells show computed values; formula bar shows the selected cell's raw source
  - [ ] Floating-point display noise is trimmed (`0.1+0.2`→`0.3`) without losing internal accuracy
  - [ ] Error tokens render in-cell
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can confirm correctness from a single glance at a screenshot — inputs, the formula on selection, and a clean computed result like `8` or `0.3` all visible together. (G1, G4, G5, US-1, FR-12, EC-1, EC-9)
  - **How we'll check:** In the dev server, set A1=2,B1=3,C1=`=A1+B1*2`, confirm C1 shows 8 and selecting C1 shows `=A1+B1*2` in the formula bar; enter `=0.1+0.2` elsewhere and confirm it shows `0.3`.
  - **If this fails, the user sees:** Cells show raw formulas instead of results, or results show ugly floating-point tails like `0.30000000000000004`, undermining trust in the app.

- [x] `UI-E3` | MEDIUM | Plaintext copy/paste of cell and range source text with grid-boundary truncation

  > Test: Select A1 (value `5`), Ctrl+C, select B1, Ctrl+V — B1 contains `5`; select A1 (formula `=B1+1`), copy, paste into C1 — C1 contains `=B1+1` verbatim; select A1:B2, copy, paste at D1 — D1,D2,E1,E2 receive the block; paste a 2×2 block at J20 — only the in-bounds cell receives data.

  **Entry Criteria:** `UI-E2` complete; cells store raw source text and support selection; in-cell editing/commit works.

  **Exit Criteria:** Selecting a cell or contiguous range and pressing Ctrl+C/Cmd+C copies the raw source text (literal or `=formula`) as tab-delimited (columns) / newline-delimited (rows) plaintext; Ctrl+V/Cmd+V pastes tab/newline-delimited plaintext into the grid starting at the selected cell; formulas are pasted verbatim with no reference rewriting; pastes exceeding row 20 or column J are silently truncated to fit grid bounds; work committed on `phase/ui-e3`.

  **TDD Requirements:**
  - `copy-paste-single.test.tsx`: Asserts copying A1=`5` and pasting into B1 makes B1=`5`.
  - `copy-paste-formula.test.tsx`: Asserts copying A1=`=B1+1` and pasting into C1 makes C1=`=B1+1` (no rewrite).
  - `copy-paste-range.test.tsx`: Asserts copying A1:B2 and pasting at D1 fills D1,D2,E1,E2 with corresponding sources.
  - `paste-truncate.test.tsx`: Asserts a paste at J20 that would exceed bounds is silently clipped to fit.

  **Done Criteria:**
  - [ ] Copy emits tab/newline-delimited raw source text for cell or range
  - [ ] Paste writes plaintext starting at the selected cell; formulas verbatim, no reference rewriting
  - [ ] Pastes exceeding grid bounds are silently truncated
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user can copy a cell or block and paste its raw text elsewhere in the grid, with formulas copied exactly as written and anything past the grid edge quietly dropped. (US-5, FR-14, EC-17, EC-18, EC-19, EC-20)
  - **How we'll check:** In the dev server, set A1=`5`, copy A1, paste into B1 (expect `5`); set A1=`=B1+1`, copy, paste into C1 (expect `=B1+1` verbatim); select A1:B2, copy, paste at D1 and confirm D1/D2/E1/E2 fill; paste a 2×2 block at J20 and confirm out-of-bounds cells are dropped.
  - **If this fails, the user sees:** Copy/paste does nothing, formulas get their references silently changed, or pasting near the grid edge errors instead of clipping.

- [x] `INT-E1` | HIGH | End-to-end verification of all observable frames and final static build

  > Test: Run the acceptance suite against the dev server covering EC-1 through EC-20, then run `npm run build` and load `dist/` to confirm the same precedence/recalc/cycle/function/copy-paste frames pass in the production build.

  **Entry Criteria:** `UI-E3` complete; all functional phases done; engine, display, and copy/paste integrated.

  **Exit Criteria:** An end-to-end acceptance suite exercises every PRD edge case (EC-1–EC-20) against the running app and passes; `npm run build` produces a static `dist/` folder that, when served, passes the same observable frames; the app remains responsive throughout with no console errors; work committed on `phase/int-e1`.

  **TDD Requirements:**
  - `e2e-precedence-recalc.test.ts`: Asserts EC-1–EC-4 (precedence, parentheses, transitive and order-independent recalc).
  - `e2e-errors-cycles.test.ts`: Asserts EC-5–EC-7, EC-12–EC-15 (cycles, div/0, propagation, out-of-grid, self/multi-node).
  - `e2e-functions-float.test.ts`: Asserts EC-8, EC-9, EC-16 (aggregate recalc, float display, mixed range contents).
  - `e2e-copy-paste.test.ts`: Asserts EC-17–EC-20 (single, formula, range, truncated paste).
  - `e2e-build-smoke.test.ts`: Asserts the served `dist/` build passes EC-1, EC-5, and EC-8.

  **Done Criteria:**
  - [ ] All edge cases EC-1–EC-20 pass against the running app
  - [ ] `npm run build` produces a static `dist/` that passes the same observable frames when served
  - [ ] App stays responsive with no console errors during the suite
  - [ ] All tests in TDD Requirements pass
  - [ ] Reviewer agent has approved the phase output

  **Behavioral Verification:**
  - **User-observable:** The user gets a finished, deployable spreadsheet where every promised behavior — correct precedence, instant recalc, safe error handling, working functions, and copy/paste — holds in the real built app. (G1–G5, NFR-1, NFR-2, NFR-5)
  - **How we'll check:** Run the acceptance suite against the dev server confirming EC-1 through EC-20 pass, then run `npm run build`, serve `dist/`, and re-confirm the precedence (EC-1), cycle (EC-5), and aggregate (EC-8) frames pass in the production build.
  - **If this fails, the user sees:** Some promised behavior works in development but breaks in the deployed app, or the build cannot be served as a static site.