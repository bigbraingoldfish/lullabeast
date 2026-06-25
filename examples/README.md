# Examples

Reference demo projects — the plain-English **PRD** and the **roadmap** generated from it for
each demo. They show the kind of input Lullabeast turns into shipped software, and double as
fixtures you can point a run at.

```
examples/
└── <demo-project>/
    ├── PRD.md        # plain-English product description (the pipeline input)
    └── roadmap.md    # the phased roadmap converted from the PRD
```

One folder per demo project, named in `kebab-case` (e.g. `tic-tac-toe/`, `snake-game/`).

Related imagery lives under [`docs/assets/`](../docs/assets): demo run GIFs in
`docs/assets/demos/`, UI screenshots in `docs/assets/screenshots/`.
