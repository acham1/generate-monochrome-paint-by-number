"""Export the tonal relief as a 3-D model.

Each region becomes a flat plateau whose height follows its brightness, so
light areas stand proud and dark ones sit back. Lit from the side, the steps
cast their own shadows and the picture reads as relief rather than as paint.

The mesh is built one column per sampled pixel: a top face, a bottom face, and
a vertical wall wherever a column meets a shorter neighbour or the outside. That
is more triangles than merging coplanar neighbours would give, but every edge is
shared by exactly two faces, so the result is watertight by construction rather
than by a slicer's repair pass. `--stl-px` trades that cost off against detail.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

STL_RECORD = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])


@dataclass(frozen=True)
class MeshOptions:
    """Physical shape of the printed relief, in millimetres."""

    max_mm: float = 170.0
    """Longest edge of the finished piece. The other follows from the image's
    aspect, so one figure fits either orientation to the same bed."""
    relief_mm: float = 8.0
    """Height climbed between the darkest tone and the lightest. This is the
    knob that decides whether the relief reads: the shading comes from steps
    shadowing their neighbours, so shallow steps give little more than an
    outline. Below about 4mm at this width the picture barely appears."""
    base_mm: float = 2.0
    """Solid slab under the darkest tone, so nothing is paper thin."""
    nozzle_mm: float = 0.4
    """Sampling pitch: one grid column per nozzle width. A printer cannot
    resolve anything narrower than its bead, and sampling coarser than that
    throws away detail it could have given, so the grid is derived from this
    rather than set independently."""
    invert: bool = False
    """Raise the dark tones instead. For a backlit piece, where thick reads dark."""
    seam_mm: float = 0.0
    """Width of a raised seam tracing every region boundary. 0 leaves them bare.

    A seam sharpens each boundary without flattening the tonal steps, because
    the plateaus keep their heights and the seam merely rides over them. Walls
    raised to a single height would sharpen the boundaries too, but they discard
    what the geometry encodes for free: the step between two regions is
    proportional to their difference in tone."""
    seam_rise_mm: float = 1.5
    """How far the seam stands above the higher of the two plateaus it divides.

    Kept small on purpose. A seam this size is a lip buttressed by the plateau
    behind it, where a full-height wall between two dark regions is a
    free-standing fin and prints badly."""
    border_mm: float = 0.0
    """Width of a raised frame around the picture. 0 leaves the edge bare."""
    border_rise_mm: float = 2.0
    """How far the frame stands above the lightest tone. Level with it, the
    frame vanishes wherever the picture is light at the edge, so it needs a
    step of its own."""
    border_gap_mm: float = 0.0
    """A recessed gutter between frame and picture. Reads as a rebate and
    separates the two crisply."""


@dataclass(frozen=True)
class LithophaneOptions:
    """A plate whose thickness carries the picture, read by light through it.

    Where the relief depends on shadows raking across a surface, this depends on
    light passing through: thin lets it through and reads light, thick blocks it
    and reads dark. That inverts the mapping, and it wants the whole plate thin,
    since the contrast comes from the ratio between thinnest and thickest rather
    than from any absolute depth.
    """

    max_mm: float = 170.0
    """Longest edge of the finished plate."""
    nozzle_mm: float = 0.4
    """Sampling pitch, one grid column per bead, as for the relief."""
    thin_mm: float = 0.4
    """Thickness under the lightest tone - two layers at 0.2mm.

    Thin is what buys contrast, since brightness is what the thinnest tone can
    pass. Two layers is the floor worth trying and it has two risks: the largest
    patch of lightest tone becomes a membrane a few centimetres across (29mm on
    one photo in this set), and with only two layers there is nothing to average
    out the extrusion paths, so they can show as striping against the light.
    Both are cheap to check on a test strip before committing to a plate."""
    thick_mm: float = 3.0
    """Thickness under the darkest tone. Light falls off steeply with thickness,
    so this reaches near-opaque well before the plate becomes chunky."""
    layer_mm: float = 0.2
    """Snap every thickness to a whole number of layers. Printed flat, thickness
    IS layer count, so unsnapped values round unpredictably at slice time and two
    tones can collapse into the same number of layers."""
    step_layers: int = 2
    """If set, space the tones this many whole layers apart and derive the thick
    end from it, ignoring `thick_mm` and `gamma`.

    Worth preferring. Snapping a thin-to-thick range to layers only gives an
    even ladder when the layer count happens to divide by the number of gaps:
    0.6 to 3.0mm at 0.2mm is 12 layers over 5 gaps, which comes out 2, 3, 2, 3,
    2 and puts a wobble in the tone ladder that has nothing to do with the
    picture."""
    gamma: float = 1.0
    """Shapes the tone-to-thickness curve. Above 1 thins the midtones, brightening
    them; below 1 thickens them. The right value depends on how much your
    filament attenuates, so it wants calibrating against a test print.

    Ignored when `step_layers` is set, since an even ladder is by definition
    a linear one."""
    border_mm: float = 0.0
    """Width of a solid opaque frame. Reads black, and stiffens a thin plate."""


def lithophane_thickness(paint_values, opts: LithophaneOptions) -> np.ndarray:
    """Thickness in mm for each tone, thickest for the darkest.

    Snapped to whole layers, because printed flat a tone's thickness is just its
    layer count and the slicer will round to one anyway.
    """
    brightness = np.asarray(paint_values, dtype=np.float64)

    if opts.step_layers > 0 and opts.layer_mm > 0:
        # Rank the tones and walk up in whole layers, so the ladder is even by
        # construction rather than by luck of the arithmetic.
        base_layers = max(round(opts.thin_mm / opts.layer_mm), 1)
        rank = np.argsort(np.argsort(-brightness))  # 0 for the lightest
        return (base_layers + rank * opts.step_layers) * opts.layer_mm

    darkness = np.clip(1.0 - brightness, 0.0, 1.0) ** opts.gamma
    thickness = opts.thin_mm + (opts.thick_mm - opts.thin_mm) * darkness
    if opts.layer_mm > 0:
        layers = np.maximum(np.round(thickness / opts.layer_mm), 1.0)
        thickness = layers * opts.layer_mm
    return thickness


def ladder_steps(thickness, layer_mm: float):
    """Layer counts between consecutive tones, for checking the ladder is even."""
    if layer_mm <= 0:
        return []
    layers = np.round(np.asarray(thickness) / layer_mm).astype(int)
    return np.abs(np.diff(np.sort(layers))).tolist()


def write_lithophane_stl(
    region_map, region_levels, paint_values, opts: LithophaneOptions, path
) -> dict:
    """Build and write the lithophane plate."""
    thickness = lithophane_thickness(paint_values, opts)
    # Reuse the relief's sampler by handing it a palette that is already in
    # millimetres: base 0, relief 1, so height comes out equal to thickness.
    sampler = MeshOptions(max_mm=opts.max_mm, nozzle_mm=opts.nozzle_mm, base_mm=0.0, relief_mm=1.0)
    heights = height_field(region_map, region_levels, thickness, sampler)
    pixel_mm = pitch_mm(heights.shape, sampler)
    if opts.border_mm > 0:
        _set_border(heights, max(round(opts.border_mm / pixel_mm), 1), float(thickness.max()))
    triangles = build(heights, pixel_mm)
    write_stl(triangles, path)

    layers = thickness / opts.layer_mm if opts.layer_mm > 0 else thickness
    return {
        "triangles": len(triangles),
        "width_mm": heights.shape[1] * pixel_mm,
        "depth_mm": heights.shape[0] * pixel_mm,
        "height_mm": float(heights.max()),
        "grid": heights.shape,
        "thickness_mm": [round(float(t), 3) for t in thickness],
        "layers": [int(round(float(n))) for n in layers] if opts.layer_mm > 0 else None,
        "distinct_thicknesses": int(len(np.unique(np.round(thickness, 6)))),
    }


def write_lithophane_test_strip(
    tones: int, opts: LithophaneOptions, path, patch_mm: float = 20.0
) -> dict:
    """A row of patches, one per tone, for holding up to a light.

    The tone-to-thickness curve depends on how much a given filament attenuates,
    which no amount of geometry can tell you. Print this, hold it up, and see
    whether the steps look evenly spaced: if the middle patches read too dark,
    raise --litho-gamma.
    """
    thickness = lithophane_thickness(np.linspace(0.0, 1.0, tones), opts)
    pixel_mm = opts.nozzle_mm
    patch_px = max(round(patch_mm / pixel_mm), 2)
    heights = np.repeat(thickness[::-1], patch_px)[None, :].repeat(patch_px, axis=0)
    triangles = build(heights, pixel_mm)
    write_stl(triangles, path)
    return {
        "triangles": len(triangles),
        "width_mm": heights.shape[1] * pixel_mm,
        "depth_mm": heights.shape[0] * pixel_mm,
        "thickness_mm": [round(float(t), 3) for t in thickness],
        "order": "lightest (thinnest) first",
    }


def pitch_mm(grid_shape: tuple[int, int], opts: MeshOptions) -> float:
    """Millimetres per grid column, once the picture is sampled."""
    return opts.max_mm / max(grid_shape)


def height_field(region_map, region_levels, paint_values, opts: MeshOptions) -> np.ndarray:
    """Sample the picture onto a grid of column heights in millimetres."""
    levels = np.asarray(region_levels)
    values = np.asarray(paint_values, dtype=np.float64)
    h, w = region_map.shape

    columns = max(round(opts.max_mm / opts.nozzle_mm), 2)
    scale = columns / max(h, w)
    new_h, new_w = max(round(h * scale), 2), max(round(w * scale), 2)
    rows = np.linspace(0, h - 1, new_h).round().astype(int)
    cols = np.linspace(0, w - 1, new_w).round().astype(int)
    sampled = region_map[np.ix_(rows, cols)]

    brightness = values[levels[sampled]]
    if opts.invert:
        brightness = 1.0 - brightness
    heights = opts.base_mm + opts.relief_mm * brightness

    if opts.seam_mm > 0:
        _raise_seams(heights, sampled, pitch_mm(heights.shape, opts), opts)

    if opts.border_mm > 0:
        pixel_mm = pitch_mm(heights.shape, opts)
        gap = max(round(opts.border_gap_mm / pixel_mm), 0)
        frame = max(round(opts.border_mm / pixel_mm), 1)
        # The gutter first, then the frame over its outer part, so the frame
        # keeps its full width and the gutter sits between it and the picture.
        if gap:
            _set_border(heights, frame + gap, opts.base_mm)
        _set_border(heights, frame, opts.base_mm + opts.relief_mm + opts.border_rise_mm)
    return heights


def _boundary_mask(ids: np.ndarray, width_px: int) -> np.ndarray:
    """Pixels within `width_px` of a change in region id.

    Comparing each way and keeping both sides already gives a two pixel band,
    so only wider seams need dilating.
    """
    mask = np.zeros(ids.shape, dtype=bool)
    differs = ids[:, :-1] != ids[:, 1:]
    mask[:, :-1] |= differs
    mask[:, 1:] |= differs
    differs = ids[:-1, :] != ids[1:, :]
    mask[:-1, :] |= differs
    mask[1:, :] |= differs
    extra = (width_px - 2) // 2
    if extra > 0:
        mask = ndimage.binary_dilation(mask, iterations=extra)
    return mask


def _raise_seams(heights: np.ndarray, ids: np.ndarray, pixel_mm: float, opts: MeshOptions) -> None:
    """Lift a seam over every region boundary, in place.

    The seam follows the terrain rather than sitting at one height: each pixel
    rises above the tallest plateau it touches, so the tonal steps underneath
    survive and the seam stays a shallow lip wherever it goes.
    """
    width_px = max(round(opts.seam_mm / pixel_mm), 1)
    mask = _boundary_mask(ids, width_px)
    if not mask.any():
        return
    local_max = ndimage.grey_dilation(heights, size=2 * width_px + 1)
    heights[mask] = local_max[mask] + opts.seam_rise_mm


def _set_border(heights: np.ndarray, band: int, value: float) -> None:
    """Flatten a band of `band` pixels around the edge to `value`, in place."""
    band = min(band, heights.shape[0] // 2, heights.shape[1] // 2)
    if band < 1:
        return
    heights[:band, :] = value
    heights[-band:, :] = value
    heights[:, :band] = value
    heights[:, -band:] = value


def _oriented(a, b, c, d, outward):
    """Wind each quad so its face normal points along `outward`."""
    normal = np.cross(b - a, c - a)
    flip = (normal * outward).sum(axis=1) < 0
    b, d = np.where(flip[:, None], d, b), np.where(flip[:, None], b, d)
    return a, b, c, d


def _triangles(a, b, c, d) -> np.ndarray:
    """Split quads a-b-c-d into two triangles apiece, keeping the winding."""
    return np.concatenate([np.stack([a, b, c], axis=1), np.stack([a, c, d], axis=1)])


def _corner(x, y, z) -> np.ndarray:
    return np.stack([x.ravel(), y.ravel(), z.ravel()], axis=1)


def build(heights: np.ndarray, pixel_mm: float) -> np.ndarray:
    """Turn a grid of column heights into triangles, as an (N, 3, 3) array."""
    rows, cols = heights.shape
    row_idx, col_idx = np.indices((rows, cols))

    x0 = col_idx * pixel_mm
    x1 = x0 + pixel_mm
    # Row 0 is the top of the picture, so flip it onto the far side in y.
    y0 = (rows - 1 - row_idx) * pixel_mm
    y1 = y0 + pixel_mm
    top = heights
    floor = np.zeros_like(heights)

    faces = []

    faces.append(
        _triangles(
            *_oriented(
                _corner(x0, y0, top),
                _corner(x1, y0, top),
                _corner(x1, y1, top),
                _corner(x0, y1, top),
                np.array([0.0, 0.0, 1.0]),
            )
        )
    )
    faces.append(
        _triangles(
            *_oriented(
                _corner(x0, y0, floor),
                _corner(x1, y0, floor),
                _corner(x1, y1, floor),
                _corner(x0, y1, floor),
                np.array([0.0, 0.0, -1.0]),
            )
        )
    )

    # A wall stands wherever a column meets a shorter neighbour, and around the
    # outside, where the neighbour is the floor. Each is emitted once, from the
    # taller column, so no wall is drawn twice.
    #
    # Every wall is cut at the same global set of heights rather than spanning
    # its own gap in one piece. Where three columns of different heights meet,
    # one-piece walls leave vertical edges that overlap without matching - a
    # 0..2 edge against a 0..1 - and the mesh is no longer closed. Splitting on
    # a shared ladder of z values makes those edges line up. There are only as
    # many rungs as there are tones, so this costs little.
    bands = np.unique(np.concatenate([[0.0], np.unique(heights)]))
    padded = np.pad(heights, 1)
    for axis, delta, outward in (
        (1, +1, np.array([1.0, 0.0, 0.0])),
        (1, -1, np.array([-1.0, 0.0, 0.0])),
        (0, +1, np.array([0.0, -1.0, 0.0])),
        (0, -1, np.array([0.0, 1.0, 0.0])),
    ):
        neighbour = np.roll(padded, -delta, axis=axis)[1:-1, 1:-1]
        for lower, upper in zip(bands[:-1], bands[1:]):
            exposed = (neighbour <= lower) & (heights >= upper)
            if not exposed.any():
                continue
            lo = np.full(int(exposed.sum()), lower)
            hi = np.full(int(exposed.sum()), upper)
            if axis == 1:  # wall faces left or right, so it lies in a plane of x
                x = np.where(delta > 0, x1, x0)[exposed]
                ya, yb = y0[exposed], y1[exposed]
                quad = (
                    np.stack([x, ya, lo], axis=1),
                    np.stack([x, yb, lo], axis=1),
                    np.stack([x, yb, hi], axis=1),
                    np.stack([x, ya, hi], axis=1),
                )
            else:  # wall faces front or back, in a plane of y
                y = np.where(delta > 0, y0, y1)[exposed]
                xa, xb = x0[exposed], x1[exposed]
                quad = (
                    np.stack([xa, y, lo], axis=1),
                    np.stack([xb, y, lo], axis=1),
                    np.stack([xb, y, hi], axis=1),
                    np.stack([xa, y, hi], axis=1),
                )
            faces.append(_triangles(*_oriented(*quad, outward)))

    return np.concatenate(faces).astype(np.float32)


def write_stl(triangles: np.ndarray, path) -> None:
    """Write triangles as a binary STL."""
    edge_a = triangles[:, 1] - triangles[:, 0]
    edge_b = triangles[:, 2] - triangles[:, 0]
    normal = np.cross(edge_a, edge_b)
    length = np.linalg.norm(normal, axis=1, keepdims=True)
    normal = np.divide(normal, length, out=np.zeros_like(normal), where=length > 0)

    records = np.zeros(len(triangles), dtype=STL_RECORD)
    records["normal"] = normal
    records["v"] = triangles
    with open(path, "wb") as handle:
        handle.write(b"monochrome relief".ljust(80, b"\0"))
        handle.write(np.uint32(len(triangles)).tobytes())
        handle.write(records.tobytes())


# A step this many times taller than the narrowest plateau is wide prints as a
# thin wall that wobbles under the nozzle. Past roughly twice this it snaps off.
# See "How deep to make the relief" in the README for where the figure comes from.
WALL_RATIO_WARN = 10.0


def wall_ratio(step_mm: float, narrowest_mm: float) -> float:
    """How tall each step stands relative to the thinnest plateau's width.

    This, not the relief height on its own, is what decides whether a relief can
    be printed: the same 25mm of relief is comfortable on broad regions and
    unprintable on slivers.
    """
    if narrowest_mm <= 0:
        return float("inf")
    return step_mm / narrowest_mm


def write_relief_stl(
    region_map,
    region_levels,
    paint_values,
    opts: MeshOptions,
    path,
    narrowest_px: float | None = None,
) -> dict:
    """Build and write the relief, returning its physical size for reporting."""
    heights = height_field(region_map, region_levels, paint_values, opts)
    pixel_mm = pitch_mm(heights.shape, opts)
    triangles = build(heights, pixel_mm)
    write_stl(triangles, path)

    tones = len(np.asarray(paint_values))
    step_mm = opts.relief_mm / max(tones - 1, 1)
    info = {
        "triangles": len(triangles),
        "width_mm": heights.shape[1] * pixel_mm,
        "depth_mm": heights.shape[0] * pixel_mm,
        "height_mm": float(heights.max()),
        "grid": heights.shape,
        "step_mm": step_mm,
    }
    if narrowest_px is not None:
        # label_radius is an inscribed radius in source pixels; the printed
        # piece spans max_mm across the source map's longest edge.
        source_mm = opts.max_mm / max(region_map.shape)
        narrowest_mm = 2 * narrowest_px * source_mm
        info["narrowest_mm"] = narrowest_mm
        info["wall_ratio"] = wall_ratio(step_mm, narrowest_mm)
    return info
