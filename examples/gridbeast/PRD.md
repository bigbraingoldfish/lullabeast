# GridBeast

## Problem Statement
Spreadsheet apps are heavyweight tools that require accounts, installation, and steep learning curves. There is no lightweight, zero-setup option for quick scratch calculations or for demonstrating that a formula engine can be built correctly. A mini spreadsheet that evaluates cell formulas — with operator precedence, parentheses, cell references, live dependency recalculation, and graceful error handling — fills this gap. As a Lullabeast showcase, the correctness of the formula engine is the primary differentiator: formula evaluation is precisely where automated code generation is most visibly error-prone, and a clean implementation disproportionately builds viewer trust.

## Goals & Success Metrics

| # | Goal | Success Criterion |
|---|------|-------------------|
| G1 | Correct precedence | `* /` always bind tighter than `+ -`; parentheses override. `=A1+B1*2` with A1=2, B1=3 shows **8**, never 10 — verifiable by mental math on screen. |
| G2 | Correct recalc | Editing any cell updates all directly and transitively dependent cells, in dependency-correct order, regardless of the cells' physical position in the grid. |
| G3 | No catastrophic failure | Circular references and malformed formulas produce a visible error token and **never** hang, freeze, or crash the page. |
| G4 | Glance-verifiable | A viewer can confirm correctness from a single screenshot of inputs + formula + result, without trusting the app. |
| G5 | Looks like a real spreadsheet | Labeled grid, formula bar, computed values in cells, source formula on selection — reads as genuine software, not a demo stub. |

## User Stories

- **US-1 — Skeptical viewer:** "I type `=A1+B1*2` with A1=2 and B1=3; I expect to see 8, not 10. This one cell tells me whether a real parser exists or someone string-concatenated slop."
- **US-2 — Scratch user:** "I want a quick grid for a one-off calculation without opening Excel, creating an account, or installing anything."
- **US-3 — Chain watcher:** "I change one upstream number and watch the entire dependent chain update instantly — satisfying and confidence-building."
- **US-4 — Stress tester:** "I deliberately create a circular reference. The app warns me with `#CIRC!` and remains fully responsive — I can keep editing other cells."
- **US-5 — Copy/paste user:** "I select a cell or range, copy the raw value/formula text, and paste it elsewhere in the grid. Plaintext only — no formatting needed."

## Functional Requirements

- **FR-1 — Grid.** A fixed grid with letter-labeled columns and number-labeled rows (default 10 columns A–J × 20 rows). Cells are individually selectable by click.
- **FR-2 — Selection & formula bar.** Selecting a cell shows its **raw content** (literal text or `=formula`, not the computed value) in a formula bar. Editing is possible both in the formula bar and in-cell; commit on Enter / blur.
- **FR-3 — Literal entry.** Content not starting with `=` is a literal: stored as a **number** if it parses as one, otherwise as **text**. Text literals are displayed verbatim and are never treated as formulas or references.
- **FR-4 — Formula entry.** Content starting with `=` is a formula, parsed and evaluated.
- **FR-5 — Operators & grouping.** Support `+ - * /` and parentheses, with correct precedence (`* /` over `+ -`), left-associativity, and arbitrary nesting. Support **unary minus** (`=-A1`, `=3*-2`).
- **FR-6 — Cell references.** A reference (e.g. `A1`, `B12`) resolves to the **computed value** of that cell. A reference to an empty cell evaluates to **0** in arithmetic context.
- **FR-7 — Dependency tracking & recalc.** Editing a cell recalculates every cell that depends on it, directly or transitively. Evaluation order is **dependency-correct (topological)**, not grid/row order — a dependency placed physically below or after its dependent must still resolve correctly.
- **FR-8 — Cycle detection.** Circular references (`A1==B1`, `B1==A1`; or a self-reference `A1==A1`) are detected and produce a **`#CIRC!`** token in **all** involved cells. The engine must **never** infinite-loop, hang, or freeze the tab.
- **FR-9 — Error tokens.** A defined error set, each shown in-cell as its token: `#DIV/0!` (division by zero), `#CIRC!` (cycle), `#REF!` (reference to an out-of-grid cell), `#NAME?` / `#ERROR!` (unparseable formula).
- **FR-10 — Error propagation.** A formula referencing an errored cell yields an **error**, not `NaN`, `undefined`, or a crash. `=A1+1` where A1 is `#DIV/0!` is itself an error.
- **FR-11 — Aggregate functions over ranges.** `=SUM(A1:A3)`, `=AVERAGE(A1:A3)`, `=MIN(A1:A3)`, `=MAX(A1:A3)` aggregate a contiguous rectangular range and recalculate when **any** member cell changes. Empty cells in the range contribute 0 to `SUM`, are ignored for `AVERAGE`, `MIN`, and `MAX` (with `MIN`/`MAX` returning `#REF!` if the range contains no valid numeric values).
- **FR-12 — Display formatting.** Cells show **computed values**; the selected cell's source is shown in the formula bar. Numeric display is trimmed to avoid floating-point noise — `=0.1+0.2` displays as `0.3`, not `0.30000000000000004`.
- **FR-13 — Malformed-input robustness.** A malformed formula (`=A1+`, `=*3`, `=()`, unbalanced parens) yields a parse-error token (FR-9), never a crash and never a silently-wrong value.
- **FR-14 — Plaintext copy/paste.** Selecting a cell or contiguous range, pressing Ctrl+C / Cmd+C copies the **raw source text** (literal or `=formula`) of each selected cell as tab-delimited plaintext. Pressing Ctrl+V / Cmd+V pastes tab/newline-delimited plaintext into the grid starting at the selected cell. No formatting is preserved; only source text is transferred.

## Edge Cases

- **EC-1 — Precedence.** A1=2, B1=3, C1=`=A1+B1*2` → C1 must show **8**, not 10. This is the single most important correctness frame.
- **EC-2 — Parentheses override.** D1=`=(A1+B1)*2` with same inputs → D1 shows **10**.
- **EC-3 — Transitive recalc.** A1=2, B1=`=A1*2` (shows 4), C1=`=B1+1` (shows 5). Change A1 to 5 → B1 becomes **10**, C1 becomes **11**, both update in one action.
- **EC-4 — Order-independent recalc.** Place the dependency after its dependent (e.g. C5=`=C6+1`, then C6=4) → C5 correctly shows **5**. Proves topological evaluation, not row-order.
- **EC-5 — Cycle, no hang.** A1=`=B1`, B1=`=A1` → both cells show **`#CIRC!`** and the page stays responsive (other cells still editable).
- **EC-6 — Division by zero.** Any cell=`=5/0` → shows **`#DIV/0!`** (not `Infinity`, not a crash).
- **EC-7 — Error propagation.** With a `#DIV/0!` cell present, `=<that cell>+1` → shows an **error token**, not `NaN`.
- **EC-8 — Aggregate range recalc.** A1:A3 = 10, 20, 30; E1=`=SUM(A1:A3)` shows **60**. Change A2 to 25 → E1 shows **65**. Same pattern holds for `AVERAGE` (→ 20, then 21.67), `MIN` (→ 10), `MAX` (→ 30).
- **EC-9 — Float display.** `=0.1+0.2` → shows **0.3**, not the long floating-point tail.
- **EC-10 — Text literal.** Typing `Price` into a cell → displays **`Price`**; it is not treated as a formula or a reference and does not error.
- **EC-11 — Empty cell reference.** A formula referencing an empty cell evaluates to **0** in arithmetic context.
- **EC-12 — Out-of-grid reference.** `=Z99` on a 10×20 grid → **`#REF!`**.
- **EC-13 — Malformed formula.** `=A1+`, `=*3`, `=()`, unbalanced parens → parse-error token, never crash.
- **EC-14 — Cycle in all involved cells.** A1=`=B1`, B1=`=C1`, C1=`=A1` → **all three** cells show `#CIRC!`.
- **EC-15 — Self-reference.** A1=`=A1` → **A1 shows `#CIRC!`**.
- **EC-16 — Mixed range contents.** A1=10, A2=`hello`, A3=20; B1=`=SUM(A1:A3)` → **30** (text ignored), B2=`=AVERAGE(A1:A3)` → **15** (text ignored), B3=`=MAX(A1:A3)` → **20**.
- **EC-17 — Copy/paste single cell.** Select A1 (value `5`), copy, select B1, paste → B1 now contains `5`.
- **EC-18 — Copy/paste formula.** Select A1 (formula `=B1+1`), copy, select C1, paste → C1 now contains `=B1+1` (formula is copied verbatim; no reference rewriting).
- **EC-19 — Copy/paste range.** Select A1:B2 (2×2 block), copy, select D1, paste → D1, D2, E1, E2 receive the corresponding source texts in a tab-delimited / newline-delimited transfer.
- **EC-20 — Paste outside grid.** Select J20 (last cell), paste a 2×2 block → only J20 and J21 (if J21 existed) receive data; J21 is out of bounds so it is silently clipped. Actually, since the grid is exactly 10×20, any paste that would exceed row 20 or column J is **silently truncated** to fit within the grid bounds.

## Non-Functional Requirements

- **NFR-1 — Client-only.** All computation is client-side. No backend, no server, no API calls, no auth.
- **NFR-2 — Static deployability.** Build output is a single static `dist/` folder, deployable to any static host (GitHub Pages / Cloudflare Pages) with zero secrets.
- **NFR-3 — No eval shortcuts.** `eval()` / `new Function()` on formula strings are explicitly forbidden. The engine must implement a real tokenizer/parser/evaluator (recursive-descent or shunting-yard → AST or RPN).
- **NFR-4 — No grid-order recalc.** Recalculation order must be derived from the dependency graph, not the cells' physical position.
- **NFR-5 — Responsiveness.** All edits, recalculations, and error handling must complete without perceptible delay on modern hardware.
- **NFR-6 — Desktop-first responsive.** The app is designed for desktop but should remain usable on smaller screens.

## Dependencies & Integrations

- **Frontend framework:** React with Vite.
- **State:** In-memory (React state or a plain reactive store). No persistence — the grid is cleared on page refresh.
- **Build output:** Single static `dist/` folder.
- **No external APIs, no auth, no env keys.**
- **No persistence / localStorage for MVP.**

## Milestones & Timeline

- **M1 — Grid & Literals (Day 1–2)**
  - Render labeled 10×20 grid.
  - Cell selection (click).
  - In-cell and formula-bar editing.
  - Literal entry: number parsing + text fallback.
  - Display computed vs. raw content correctly.

- **M2 — Formula Engine Core (Day 3–5)**
  - Tokenizer for `=...` formulas.
  - Parser with correct precedence (`* /` over `+ -`), parentheses, unary minus.
  - Cell reference resolution (A1, B12).
  - Evaluator returning numeric results.
  - Error tokens for malformed formulas and out-of-grid refs.

- **M3 — Dependency Graph & Recalc (Day 6–7)**
  - Build dependency graph from cell references.
  - Topological-order recalculation on cell edit.
  - Cycle detection → `#CIRC!` token in all involved cells.
  - Error propagation through dependency chain.

- **M4 — Functions, Copy/Paste & Polish (Day 8)**
  - Implement `SUM`, `AVERAGE`, `MIN`, `MAX` over contiguous ranges.
  - Plaintext copy/paste (single cell and range).
  - Float display trimming (`0.1+0.2` → `0.3`).
  - All user-observable verification criteria (V-1 through V-10, plus function and copy/paste frames) pass.
  - Final static build and smoke test.

## Risks & Mitigations

- **R1 — Formula engine correctness.** Getting precedence, associativity, and unary minus right is non-trivial. **Mitigation:** Explicit AST/RPN representation, unit-test each operator level separately, and verify the precedence frame (EC-1) as a gate check.
- **R2 — Cycle detection bugs.** A missed cycle leads to an infinite loop. **Mitigation:** Graph-coloring (white/gray/black) or visited-set during DFS; test self-reference, two-node cycle, and three-node cycle.
- **R3 — Floating-point display noise.** `0.1+0.2` → `0.30000000000000004` breaks the confidence goal. **Mitigation:** Trim to a reasonable decimal precision for display (e.g. `parseFloat(result.toFixed(10))`) while preserving numeric accuracy internally.
- **R4 — Over-scoping.** The brief is already tight, but expanding to more functions or features too early risks correctness. **Mitigation:** Lock `SUM` / `AVERAGE` / `MIN` / `MAX` as the only functions for MVP; defer everything else to Future Iterations.
- **R5 — Copy/paste reference rewriting temptation.** A naive approach might try to rewrite cell references on paste (`A1=B1+1` pasted to C1 becomes `=D1+1`). **Mitigation:** Copy/paste is **plaintext only** — formulas are copied verbatim. No reference rewriting. This is simpler and matches the MVP scope.

## Open Questions

*All open questions resolved. See Revision History for Q&A record.*

## Glossary & Domain Terms

- **Literal:** Cell content that does not start with `=`. Parsed as a number if possible; otherwise treated as text.
- **Formula:** Cell content starting with `=`, parsed and evaluated by the engine.
- **Cell reference:** A pointer to another cell's computed value, written as a column letter followed by a row number (e.g. `A1`, `B12`).
- **Dependency graph:** A directed graph where edges represent "cell A references cell B." Used to determine recalculation order and detect cycles.
- **Topological order:** An evaluation sequence where every cell is computed after all cells it depends on. Ensures correctness regardless of physical grid position.
- **Error token:** A human-readable string (e.g. `#DIV/0!`, `#CIRC!`) displayed in a cell when computation cannot produce a valid value.
- **Range:** A contiguous rectangular block of cells specified by two corner references (e.g. `A1:A3`), used by aggregate functions like `SUM`.
- **Aggregate function:** A function that computes a single value from a range of cells (e.g. `SUM`, `AVERAGE`, `MIN`, `MAX`).

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2026-06-17 | User | Initial concept, goals, requirements, verification criteria, tech stack, and open questions. |
| 2026-06-17 | PRD Creator | Incorporated Q&A from session 2: locked function surface (SUM + AVERAGE + MIN + MAX), grid dimensions (10×20 in-memory), cycle presentation (#CIRC! in all cells), added plaintext copy/paste (FR-14), expanded edge cases, updated milestones. |
| 2026-06-17 | PRD Creator, session 3 | Confirmed project name "GridBeast". Resolved all remaining open questions (no additional scope for MVP). Cleared Open Questions section. PRD is conversion-ready. |

> ✅ PRD CONVERSION-READY