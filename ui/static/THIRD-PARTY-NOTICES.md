# Third-party notices

The Lullabeast dashboard vendors a few third-party assets under `ui/static/` so the UI has no
external runtime dependency (no CDN calls at load time). Each is redistributed under a permissive
license compatible with this project's MIT license. The minified JavaScript bundles also retain
their own license banner inline; this file is an aggregated summary. Full license texts are at each
project's homepage.

## JavaScript (vendored, minified)

| File | Library | Version | License | Copyright |
|---|---|---|---|---|
| `react.min.js` | [React](https://react.dev) | 18.3.1 | MIT | © Meta Platforms, Inc. and affiliates |
| `react-dom.min.js` | [React DOM](https://react.dev) | 18.3.1 | MIT | © Meta Platforms, Inc. and affiliates |
| `babel.min.js` | [@babel/standalone](https://babeljs.io) | vendored build (see file header) | MIT | © The Babel authors |
| `marked.min.js` | [marked](https://marked.js.org) | 15.0.12 | MIT | © Christopher Jeffrey and contributors |
| `diff.min.js` | [jsdiff](https://github.com/kpdecker/jsdiff) | vendored build (see file header) | BSD-3-Clause | © Kevin Decker and contributors |
| `tailwind.js` | [Tailwind CSS](https://tailwindcss.com) | vendored build (see file header) | MIT | © Tailwind Labs, Inc. |

## Fonts (self-hosted, `fonts/`)

Both families are variable woff2 fonts under the [SIL Open Font License 1.1](https://openfontlicense.org),
which permits bundling and redistribution.

| File | Family | License | Copyright |
|---|---|---|---|
| `fonts/hanken-grotesk.woff2` | [Hanken Grotesk](https://github.com/marcologous/hanken-grotesk) | SIL OFL 1.1 | © The Hanken Grotesk Project Authors |
| `fonts/jetbrains-mono.woff2` | [JetBrains Mono](https://www.jetbrains.com/lp/mono/) | SIL OFL 1.1 | © JetBrains s.r.o. |

The Lullabeast mascot images under `img/` are original project assets, not third-party works.
