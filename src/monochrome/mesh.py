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

STL_RECORD = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])


@dataclass(frozen=True)
class MeshOptions:
    """Physical shape of the printed relief, in millimetres."""

    width_mm: float = 120.0
    """Width of the finished piece. Depth follows from the image's aspect."""
    relief_mm: float = 8.0
    """Height climbed between the darkest tone and the lightest. This is the
    knob that decides whether the relief reads: the shading comes from steps
    shadowing their neighbours, so shallow steps give little more than an
    outline. Below about 4mm at this width the picture barely appears."""
    base_mm: float = 2.0
    """Solid slab under the darkest tone, so nothing is paper thin."""
    px: int = 300
    """Longest edge of the sampled grid. Higher is finer and much heavier."""
    invert: bool = False
    """Raise the dark tones instead. For a backlit piece, where thick reads dark."""
    border_mm: float = 0.0
    """Width of a raised frame around the picture. 0 leaves the edge bare."""
    border_rise_mm: float = 2.0
    """How far the frame stands above the lightest tone. Level with it, the
    frame vanishes wherever the picture is light at the edge, so it needs a
    step of its own."""
    border_gap_mm: float = 0.0
    """A recessed gutter between frame and picture. Reads as a rebate and
    separates the two crisply."""


def height_field(region_map, region_levels, paint_values, opts: MeshOptions) -> np.ndarray:
    """Sample the picture onto a grid of column heights in millimetres."""
    levels = np.asarray(region_levels)
    values = np.asarray(paint_values, dtype=np.float64)
    h, w = region_map.shape

    scale = opts.px / max(h, w)
    new_h, new_w = max(round(h * scale), 2), max(round(w * scale), 2)
    rows = np.linspace(0, h - 1, new_h).round().astype(int)
    cols = np.linspace(0, w - 1, new_w).round().astype(int)
    sampled = region_map[np.ix_(rows, cols)]

    brightness = values[levels[sampled]]
    if opts.invert:
        brightness = 1.0 - brightness
    heights = opts.base_mm + opts.relief_mm * brightness

    if opts.border_mm > 0:
        pixel_mm = opts.width_mm / heights.shape[1]
        gap = max(round(opts.border_gap_mm / pixel_mm), 0)
        frame = max(round(opts.border_mm / pixel_mm), 1)
        # The gutter first, then the frame over its outer part, so the frame
        # keeps its full width and the gutter sits between it and the picture.
        if gap:
            _set_border(heights, frame + gap, opts.base_mm)
        _set_border(heights, frame, opts.base_mm + opts.relief_mm + opts.border_rise_mm)
    return heights


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


def write_relief_stl(region_map, region_levels, paint_values, opts: MeshOptions, path) -> dict:
    """Build and write the relief, returning its physical size for reporting."""
    heights = height_field(region_map, region_levels, paint_values, opts)
    pixel_mm = opts.width_mm / heights.shape[1]
    triangles = build(heights, pixel_mm)
    write_stl(triangles, path)
    return {
        "triangles": len(triangles),
        "width_mm": opts.width_mm,
        "depth_mm": heights.shape[0] * pixel_mm,
        "height_mm": float(heights.max()),
        "grid": heights.shape,
    }
