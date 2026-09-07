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
| `<name>-relief.stl` | a 3-D relief where brightness becomes height, for printing |

Pick with `--formats` (default `png,lines,svg,pdf`); add `stl` for the relief.

The printed page carries only a short identifier — by default the trailing
digits of the filename — never the filename itself, so a template can be handed
to someone without giving away which photo it came from. Change that with
`--label-from name|number|none`.

## The 3-D relief

```sh
uv run monochrome pbn photo.jpg -o out --formats stl --stl-relief 10
```

Each region becomes a flat plateau whose height follows its brightness, so light
areas stand proud and dark ones sit back.

What makes such a relief read is not surface shading -- the plateaus are flat, so
they all catch light identically -- but the steps shadowing and occluding their
neighbours. That has two consequences worth knowing before printing one:

- **`--stl-relief` is the knob that matters.** Below about 4mm at a 120mm width
  the result is close to a line drawing. Around 8-10mm the picture reads
  clearly; past 15mm the shadows start to dominate.
- **Scale relief with size.** What sets the shading is the ratio of step height
  to the width of the piece, not either alone. Printing the same file larger
  while leaving `--stl-relief` alone makes it read slightly *flatter*, so raise
  the relief in proportion. Going 120mm to 170mm wants 8mm to become about
  11mm; measured on the occlusion model that restores the contrast exactly.

### How deep to make the relief

Deeper is not better past a point, and the point arrives early. Measured on the
occlusion model, for one photo at 170mm wide with 6 tones:

| `--stl-relief` | step | contrast (AO spread) | mean brightness | solid volume |
| --- | --- | --- | --- | --- |
| 2mm | 0.4mm | 0.164 | 0.906 | 62cm³ |
| 5mm | 1.0mm | 0.239 | 0.828 | 95cm³ |
| 11mm | 2.2mm | 0.302 | 0.744 | 160cm³ |
| 25mm | 5.0mm | 0.345 | 0.657 | 313cm³ |
| 50mm | 10mm | 0.362 | 0.606 | 586cm³ |
| 100mm | 20mm | 0.368 | 0.588 | 1131cm³ |
| 178mm | 36mm | 0.370 | 0.584 | 1982cm³ |

Sixteen times the depth buys 23% more contrast, and almost all of it has arrived
by 25mm. Note the mean falling alongside: past that point the relief is not
growing more differentiated, it is going uniformly darker as everything sinks
into shadow. Volume, meanwhile, climbs linearly - 178mm of relief is around
2.5kg of PLA if solid, and days of printing.

Two limits bite before the contrast does:

- **Thin walls.** A step standing tall over a narrow plateau is a thin wall, and
  thin walls wobble under the nozzle and snap. The measure is the *ratio* of
  step height to the narrowest plateau's width, which is why there is no single
  safe relief height: the same 25mm is comfortable on broad regions and hopeless
  on slivers. FDM gets unreliable past roughly 10:1 and fails past 20:1, so the
  tool computes the ratio for the actual picture and warns above `WALL_RATIO_WARN`.
  Widen the slivers with `--min-region`, or print larger, before adding depth.
- **Parallax.** Deep steps hide their neighbours at any off-axis angle, so the
  picture only reads from dead perpendicular and you spend the rest of the time
  looking at the sides of walls. Around 2mm of step this is unnoticeable.

**11-25mm at 170mm wide is the useful range**, with 15mm a good compromise if
you want more punch than the low end. Those figures are geometry and simulation,
not a test print; treat the thin-wall thresholds as the usual FDM rule of thumb.

One caveat on the table: the occlusion model only reaches about 11mm sideways,
so it understates shadowing once steps exceed that. The saturation is real, but
the deep-relief figures flatter them.
- **It emphasises boundaries more than tone.** Occlusion pools against step
  walls, so a broad recessed plateau is barely darker than a raised one. Expect
  something closer to a woodcut than to the photograph.

Pairing it with `--palette ramp` puts the tones at even height intervals, which
suits a relief better than the fitted spacing.

| option | meaning |
| --- | --- |
| `--stl-max` | longest edge of the finished piece in mm; the other follows the aspect |
| `--stl-relief` | mm climbed from the darkest tone to the lightest |
| `--stl-base` | solid slab under the darkest tone |
| `--stl-nozzle` | nozzle width; sets the sampling pitch at one column per bead |
| `--stl-invert` | raise the dark tones instead, for a backlit piece |
| `--stl-border` | width of a raised frame in mm; 0 leaves the edge bare |
| `--stl-border-rise` | how far the frame stands above the lightest tone |
| `--stl-border-gap` | recessed gutter between frame and picture |

A frame earns its place for two reasons that have nothing to do with taste.
Without one, the outer band of the picture is anomalously bright: interior
regions are occluded by their neighbours and edge regions have nothing outside
them to do the same. A raised frame restores that, and its inward shadow defines
where the picture stops. It also stiffens what is otherwise a large flat plate.

Give it a rise of its own rather than levelling it with the lightest tone, or it
disappears wherever the picture happens to be light at the edge. `--stl-border 6
--stl-border-gap 2` reads well. The frame is applied to the height field before
the mesh is built, so it joins the same ladder of wall heights and the surface
stays closed; on real photographs it slightly *reduces* the pinched corners, by
flattening the noisiest part of the edge.

What a frame does not do is fix the interior. Broad plateaus still shade weakly
whatever surrounds them.

`--stl-max` sizes the longest edge whichever way the picture is turned, so one
figure fits both orientations to the same bed, and `--stl-nozzle` derives the
grid from it. Sampling coarser than one bead throws away detail the printer
could have given; finer only inflates the file.

The mesh is built one column per sampled pixel rather than merging coplanar
neighbours, which costs triangles but leaves the surface closed by construction:
on real photographs it comes out with no open edges and a volume matching the
columns it is made from, give or take float32 rounding. A few dozen edges per
model are shared by four faces where two regions touch only at a diagonal
corner; slicers handle those. At the default `--stl-px 300` a model is roughly
280k triangles and 13MB, so a whole folder adds up quickly.

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
