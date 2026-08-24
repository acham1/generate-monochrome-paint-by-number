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
uv run monochrome pbn "photos/Engagement Photos-09.jpg" -o out

# a whole folder, with per-photo subject framing
uv run monochrome pbn photos/ -o out --subjects subjects.json
```

Each source image produces three files:

| file | what it is |
| --- | --- |
| `<name>-mono.png` | the flat monochrome rendering — what the finished painting should look like |
| `<name>-lines.png` | the outlines and numbers as a plain raster, for on-screen review |
| `<name>-pbn.pdf` | the printable template: outlines, numbers, and the swatch key |
| `<name>-pbn.svg` | the same template as vectors, for editing or plotting |

Pick with `--formats` (default `png,lines,svg,pdf`).

## Checking the framing

A crop that slices through one of two people yields a plausible-looking template
of the wrong picture, so verify framing before generating:

```sh
uv run monochrome check photos/ --framing framing.json -o framing-check.jpg
```

Every photo is drawn as it will be framed, cropped, with its subject box
outlined — clipping is obvious at a glance. It also warns when a subject box
covers more than 90% of the frame, which means it is suppressing nothing.

## Reviewing a batch

Opening sixteen PDFs to see whether a setting helped is tedious, so tile them:

```sh
uv run monochrome sheet out --kind all --strip-prefix "Engagement Photos-"
```

That writes `proof-lines.jpg` and `proof-mono.jpg` into the directory, each tile
labelled with its region count.

## How it works

1. **Tone** (`tone.py`) — grayscale, edge-preserving denoise, then quantize to
   `--levels` flat grays. Level boundaries come from 1-D k-means on the
   histogram, so tones follow the photograph instead of an even ramp.
2. **Regions** (`regions.py`) — median filter kills specks, a majority filter
   rounds off ragged filigree, then undersized components are absorbed into the
   neighbour closest in tone. What survives is traced to polygons and given the
   point furthest from its own edge as a home for its numeral.
3. **Render** (`render.py`) — fit to the page, draw outlines and numbers, lay out
   the key.

## Keeping the region count paintable

The knobs that matter most, in rough order of effect:

- `--subject left,top,right,bottom` — where the people are, as fractions of the
  frame. Detail is preserved inside; outside, regions must be
  `--background-boost` times larger to survive, so a busy background collapses
  into a few flat shapes instead of hundreds of confetti pieces. This is the
  single biggest lever on a photo with a cluttered background.
- `--min-region` — the floor on region size, in working pixels.
- `--working-px` — the resolution regions are computed at. Lower is chunkier.
- `--levels` — how many grays to mix. More levels means more regions.
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
  "Engagement Photos-19.jpg": { "subject": [0.08, 0.22, 0.92, 1.0] },
  "Engagement Photos-16.jpg": {
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

## Notes on this photo set

The engagement photos are shot in redwood forest, so a whole frame of foliage
will happily generate a thousand unpaintable regions. What worked:

- Photos where the couple is a distant speck were dropped rather than tuned;
  no setting rescues a picture that is mostly forest.
- Wide frames were cropped onto the couple, then given a subject box. Cropping
  alone raises the region count, because it removes the very background the box
  was suppressing.
- 50–120 regions per template is a comfortable range. Under 60 reads as
  graphic and flat; over 200 is a chore to paint.
- **Measure the framing, do not eyeball it.** Boxes estimated from thumbnails
  were wrong on half the set: one crop cut a person out of the frame entirely,
  and several boxes were so generous they suppressed nothing. `monochrome check`
  exists because of this.
