# Fonts shipped with opencastor.com

All three families are licensed under the SIL Open Font License 1.1 and are
self-hosted here so the site makes zero third-party network requests. The
`woff2` files are the unmodified `latin` subsets published by Fontsource.

| File | Family | Copyright | License |
| --- | --- | --- | --- |
| `newsreader-latin-wght-normal.woff2` | Newsreader (variable weight, roman) | Copyright 2019 The Newsreader Project Authors | OFL 1.1 |
| `newsreader-latin-wght-italic.woff2` | Newsreader (variable weight, italic) | Copyright 2019 The Newsreader Project Authors | OFL 1.1 |
| `atkinson-hyperlegible-next-latin-wght-normal.woff2` | Atkinson Hyperlegible Next (variable weight) | Copyright 2024 Braille Institute of America, Inc. | OFL 1.1 |
| `ibm-plex-mono-latin-400-normal.woff2` | IBM Plex Mono (400) | Copyright 2017 IBM Corp. | OFL 1.1 |

Full license text: <https://openfontlicense.org/open-font-license-official-text/>

Nothing here is subsetted beyond Fontsource's own `latin` block. If the page
budget ever needs it, subset with `pyftsubset` against the glyphs the page
actually uses and keep this file in step.
