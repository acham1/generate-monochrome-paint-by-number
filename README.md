# monochrome

Turn photographs into monochrome paint-by-numbers templates: a printable
outline drawing, a number in every region, and a key mapping each number to the
gray it should be painted.

## Install

```sh
uv sync
```

## Use

```sh
# one photo
uv run monochrome pbn photos/portrait.jpg -o out

# a whole folder, with per-photo framing
uv run monochrome pbn photos/ -o out --framing framing.json
```

Each source image produces four files:

| file | what it is |
| --- | --- |
| `<name>-mono.png` | the flat monochrome rendering — what the finished painting should look like |
| `<name>-lines.png` | the outlines and numbers as a plain raster, for on-screen review |
| `<name>-pbn.pdf` | the printable template: outlines, numbers, and the swatch key |
| `<name>-pbn.svg` | the same template as vectors, for editing or plotting |

Pick with `--formats` (default `png,lines,svg,pdf`).

The printed page carries only a short identifier — by default the trailing
digits of the filename — never the filename itself, so a template can be handed
to someone without giving away which photo it came from. Change that with
`--label-from name|number|none`.

## Checking the framing

A crop that slices through part of the subject yields a plausible-looking
template of the wrong picture, so verify framing before generating:

```sh
uv run monochrome check photos/ --framing framing.json -o framing-check.jpg
```

Every photo is drawn as it will be framed, cropped, with its subject box
outlined — clipping is obvious at a glance. It also warns when a subject box
covers more than 90% of the frame, which means it is suppressing nothing.

## Reviewing a batch

Opening a folder of PDFs to see whether a setting helped is tedious, so tile
them:

```sh
uv run monochrome sheet out --kind all --strip-prefix "DSC_"
```

That writes `proof-lines.jpg` and `proof-mono.jpg` into the directory, each tile
labelled with its region count.

## How it works

1. **Tone** (`tone.py`) — convert to CIE **L\*** lightness, denoise while
   preserving edges, then quantize to `--levels` flat tones. Level boundaries
   come from 1-D k-means on a 256-bin histogram, so tones follow the photograph
   instead of an even ramp, and the whole pipeline reasons in L\* so that equal
   numeric steps are equal *visual* steps. Plain luma is gamma-encoded and would
   put the boundaries in the wrong places. Swatches are converted back to sRGB
   for display; the percentage printed beside each is L\*.

   Where the tones *fall* (`--mode`) and what they are *painted* (`--palette`)
   are separate decisions. Fitting boundaries to the histogram but painting an
   even black-to-white ramp gives maximum contrast without starving any tone,
   whereas spacing the boundaries evenly as well leaves the lightest tone
   covering almost nothing, since little in a photograph reaches L\* 90.
2. **Regions** (`regions.py`) — median filter kills specks, a majority filter
   rounds off ragged filigree, then undersized components are absorbed into the
   neighbour closest in tone. What survives is traced to polygons and given the
   point furthest from its own edge as a home for its numeral.
3. **Render** (`render.py`) — fit to the page, draw outlines and numbers, lay out
   the key.

## Keeping the region count paintable

The knobs that matter most, in rough order of effect:

- `--subject left,top,right,bottom` — where the subject is, as fractions of the
  frame. Detail is preserved inside; outside, regions must be
  `--background-boost` times larger to survive, so a busy background collapses
  into a few flat shapes instead of hundreds of confetti pieces. This is the
  single biggest lever on a photo with a cluttered background.
- `--min-region` — the floor on region size, in working pixels.
- `--working-px` — the resolution regions are computed at. Lower is chunkier.
- `--palette fitted|ramp` — what the tones are *painted*, as opposed to where
  they fall. `fitted` uses the value each tone was measured at; `ramp` spreads
  them evenly from black to white. This moves no region and changes no outline,
  only the grays poured into them, so it is a contrast decision rather than a
  segmentation one. Pairing `ramp` with the default k-means `--mode` gives the
  full tonal range while every tone still covers a useful share of the picture.
- `--levels` — how many tones to mix. More levels means more regions. This is
  bounded by what a person can realistically mix and keep track of, not by what
  the histogram wants; six is about the ceiling.
- `--crop left,top,right,bottom` — cut the frame down before anything else.
- `--no-crop` — ignore every crop, manifest ones included, and render full
  frames. Subject boxes still apply, so this isolates what the cropping is
  actually buying you.

`--contrast` (CLAHE) is **off** by default on purpose: local contrast
enhancement flattens a dark subject into a bright background, which is exactly
the separation a template needs. Raise it only for flat, low-contrast originals.

## framing.json

Per-photo crop and subject boxes, so a whole folder renders in one command:

```json
{
  "portrait.jpg": { "subject": [0.08, 0.22, 0.92, 1.0] },
  "wide-shot.jpg": {
    "crop":    [0.22, 0.24, 0.96, 1.0],
    "subject": [0.11, 0.11, 0.89, 1.0]
  }
}
```

A bare `[left, top, right, bottom]` list is shorthand for just the subject box.
Manifest entries override `--crop` and `--subject`. Photos absent from the
manifest get uniform detail across the whole frame, which is right for close-ups
that already fill it.

**Subject boxes are in cropped coordinates.** When an entry has both, the crop
happens first, so measure the subject box against the cropped frame rather than
the original.

## Notes from practice

Photos with cluttered natural backgrounds — foliage, undergrowth, dappled light
— are the hard case. A frame of leaf texture will happily generate a thousand
unpaintable regions, and most of the tuning here exists to deal with that.

- **Curate before tuning.** No setting rescues a picture that is mostly
  background; if the subject is a distant speck, drop the photo instead.
- **A crop and a subject box work together, not in isolation.** Cropping alone
  tends to *raise* the region count, because it removes the very background the
  box was suppressing. Expect to set both.
- **50–120 regions per template is a comfortable range.** Under 60 reads as
  graphic and flat; over 200 is a chore to paint.
- **Measure the framing, do not eyeball it.** Boxes estimated from thumbnails
  were wrong on half of a 16-photo set: one crop cut a subject out of the frame
  entirely, and several boxes were so generous they suppressed nothing.
  `monochrome check` exists because of this.
