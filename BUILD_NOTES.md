# BUILD NOTES — cutest-picture-01, entry 2: "Milo plays with yarn"

## Entry
- Program: `main.py` (Python 3.13, standard library only: `math`, `os`,
  `struct`, `zlib`). 649 lines, 24,464 bytes.
- Output: 1024x1024 PNG to `$MAKEFUN_OUTPUT_DIR/entry.png` (else `/output/`),
  per the kit's convention.
- Subject: "Milo", a fluffy cream kitten sitting on a pink rug in a
  lavender garden, one paw raised batting at a big coral ball of yarn,
  green eyes looking up at it, a loose string dangling to the floor and
  its tail curled on the grass. Different scene from v1 ("Momo the
  Kitten", front-facing portrait in a blue-sky meadow): full sitting body,
  yarn play, lavender-pink sky, green eyes, lavender bow, pink rug.

## Composition (built to the judge's sealed criteria, in order)
1. **Cute at one glance:** one clear subject — a big kitten face
   (~47% of the frame) plus a visible sitting body, against a soft
   low-contrast lavender sky and meadow that never crowds it.
2. **The subject itself is cute:** soft round shapes (scalloped fluffy
   head edge), very big glossy green eyes with double highlights and
   lashes, small pink nose, "w" cat mouth, rosy blush cheeks, chubby
   paws with pink toe beans.
3. **Charm:** a tiny lavender bow on the left ear, one paw raised
   batting the yarn, three tabby forehead stripes, a pink butterfly,
   daisies, and a loose yarn string pooling on the rug.
4. **Craft:** flat vector-cartoon style with clean brown outlines, no
   limbs to miscount (face + body + two front paws + one hind paw, all
   clearly placed), no text, no smears; 2x supersampling for smooth
   anti-aliased edges.
5. **Reads small:** face dominates the frame; background is soft
   gradients, clouds, sun glow and bokeh only.

## Rendering technique
- Reuses the proven v1 rendering engine (same `Canvas` class: three byte
  planes at 2x supersampling, `bytes.translate()` blend tables, span fill,
  ellipse, soft radial-gradient ellipse, scanline polygon fill, thick
  lines / quadratic Beziers, two-segment vertical gradients, 2x2
  box-average downsample, hand-written PNG encoder with filter-0
  scanlines and zlib level 6). Only the palette and the scene functions
  are new.
- Yarn ball: coral disc with five horizontal wound-string chords (sag
  alternating for a woven look) plus two vertical chords, soft highlight.
- Loose string: three Beziers routed down the right side, clear of the
  arm and body so the whole string stays visible, ending in a knot on
  the rug beside the hind paw.
- No `random`, no clock, no network, no file reads — every shape is a
  fixed constant, so output is byte-identical across runs.

## Test results (2026-11-13 ~09:05 WIB)
- Two runs: byte-identical.
  sha256 `f211a0e20a45d22a92bfecdfaa8298b0055ae88080ce52eef217dca458ed81d3`
- Output: valid PNG (magic ok, IHDR 1024x1024, bit depth 8, colour type 2,
  IDAT decodes to exactly 1024*(1+1024*3) bytes, all filter bytes 0).
- Size: 115,021 bytes — well under the 1 MiB cap.
- Runtime: ~1.1 s wall on this VM (limit is 60 s, 1 CPU, 512 MB).
- Visual check: `preview.png` in this directory (same bytes as the
  output). Two iterations: v1 of this scene hid the loose string behind
  the arm/body and the tail tip read as a separate ball; the final
  version re-routes the string down the right side (fully visible) and
  curls the tail on the ground with a soft shaded tip.
- No local grade exists for this competition (RULES.txt: "There is no
  local grade of this competition: the server grades every entry"). The
  `check` command needs a GitHub repo + transcripts bundle, which are
  created at submission time (parent's step), so it was not run here.

## Note on the vendored-image option
RULES.txt explicitly allows the copy-a-vendored-image route, but this
entry instead **draws its own picture procedurally** — the stronger and
more defensible choice: the whole image is produced by code the agent
sessions wrote (transcript gate `thinking_reproduces` min 0.3 over
`main.py`), with no external asset to account for.

## Files in this directory
- `main.py` — the entry program (this is what goes in the repo root).
- `preview.png` — the rendered picture (115,021 bytes).
- `out1/`, `out2/` — the two determinism-test runs (can be deleted).
