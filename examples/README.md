# Examples

Reference demo projects: the plain-English **PRD** and the **roadmap** generated from it for
each demo. They show the kind of input Lullabeast turns into shipped software, and double as
fixtures you can point a run at.

```
examples/
└── <demo-project>/
    ├── PRD.md        # plain-English product description (the pipeline input)
    └── roadmap.md    # the phased roadmap converted from the PRD
```

One folder per demo project, named in `kebab-case` (e.g. `tic-tac-toe/`, `snake-game/`).

## The first-run sample: `first-run-snake/`

[`first-run-snake/`](first-run-snake) is the bundled "hello world" project for a new
install. Unlike the reference demos above it carries the complete launchable triple, so
you can copy the folder into your projects directory and run it as-is, with no Ideas
conversion step:

- [`prd.md`](first-run-snake/prd.md): a deliberately tiny single-file Snake game
- [`roadmap.md`](first-run-snake/roadmap.md): 4 phases, gate-valid, with Behavioral
  Verification blocks
- [`verification.md`](first-run-snake/verification.md): the verification contract
  (entry point, public surface, Playwright acceptance)

The walkthrough lives in the main README under "Your first run". A CI test
(`tests/test_sample_project_preflight.py`) keeps this sample preflight-green, so if gate
or preflight requirements ever tighten, the sample breaks CI instead of breaking new
users.

Related imagery lives under [`docs/assets/`](../docs/assets): demo run GIFs in
`docs/assets/demos/`, UI screenshots in `docs/assets/screenshots/`.
