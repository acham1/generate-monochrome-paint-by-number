"""Turn a flat level map into paintable regions with outlines and label points.

Two jobs live here. First, cleanup: a raw quantized photo contains thousands of
single-pixel specks that nobody could paint, so small components are absorbed
into whichever neighbour they share the most border with. Second, geometry:
each surviving region becomes simplified outline polygons plus the point
furthest from its own edge, which is where its number goes.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from skimage import measure


@dataclass(frozen=True)
class RegionOptions:
    min_area: int = 450
    """Regions smaller than this (in working pixels) get absorbed."""
    simplify: float = 0.9
    """Douglas-Peucker tolerance in working pixels. Higher = blockier outlines."""
    speckle_filter: int = 3
    """Median filter window on the level map. 0 disables it."""
    smooth_radius: float = 2.5
    """Majority-filter radius on the level map. Rounds off filigree. 0 disables."""
    smooth_iters: int = 2
    """How many majority-filter passes to run."""
    background_boost: float = 20.0
    """Outside the subject box, regions must be this many times larger to
    survive. Collapses busy backgrounds into a few flat shapes."""
    feather: float = 0.04
    """Subject-box edge softness, as a fraction of the image's long edge."""


@dataclass
class Region:
    """One paintable area: a level, its outlines, and where its number goes."""

    level: int
    area: int
    outlines: list[np.ndarray] = field(default_factory=list)
    """Closed polygons as (N, 2) float arrays of (x, y) in working pixels."""
    label_xy: tuple[float, float] = (0.0, 0.0)
    label_radius: float = 0.0
    """Distance from label_xy to the nearest region edge; sizes the numeral."""


def subject_weight(
    shape: tuple[int, int], subject, boost: float, feather: float
) -> np.ndarray | None:
    """Per-pixel multiplier on the minimum region area.

    1.0 inside the subject box, `boost` outside, with a feathered transition so
    the detail change reads as depth rather than as a rectangle drawn on the
    picture.
    """
    if subject is None or boost <= 1.0:
        return None
    h, w = shape
    left, top, right, bottom = subject
    inside = np.zeros(shape, dtype=np.float32)
    inside[
        round(top * h) : max(round(bottom * h), round(top * h) + 1),
        round(left * w) : max(round(right * w), round(left * w) + 1),
    ] = 1.0
    if feather > 0:
        inside = ndimage.gaussian_filter(inside, sigma=feather * max(h, w))
    return (boost + (1.0 - boost) * inside).astype(np.float32)


def _despeckle(level_map: np.ndarray, window: int) -> np.ndarray:
    """Median-filter the level map so isolated pixels adopt their surroundings."""
    if window and window > 1:
        return ndimage.median_filter(level_map, size=window, mode="nearest")
    return level_map


def _majority_smooth(level_map: np.ndarray, radius: float, iters: int) -> np.ndarray:
    """Replace each pixel with the most common level in a disk around it.

    Median filtering kills specks but leaves ragged, hairline shapes that are
    miserable to paint. A majority vote over a disk rounds those off while
    leaving genuine edges — where one level clearly dominates — in place. Run as
    one box filter per level, so cost is levels x image, not per-pixel sorting.
    """
    if radius <= 0 or iters <= 0:
        return level_map

    n_levels = int(level_map.max()) + 1
    size = int(2 * round(radius) + 1)
    r = size // 2
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    disk = (yy**2 + xx**2) <= radius**2 + 1e-6

    out = level_map
    for _ in range(iters):
        votes = np.empty((n_levels,) + out.shape, dtype=np.float32)
        for level in range(n_levels):
            votes[level] = ndimage.convolve(
                (out == level).astype(np.float32), disk.astype(np.float32), mode="nearest"
            )
        out = votes.argmax(axis=0).astype(level_map.dtype)
    return out


def _adjacency(comp: np.ndarray) -> dict[tuple[int, int], int]:
    """Shared border length between every pair of 4-adjacent components."""
    pairs: dict[tuple[int, int], int] = {}
    for a, b in ((comp[:, :-1], comp[:, 1:]), (comp[:-1, :], comp[1:, :])):
        differ = a != b
        if not differ.any():
            continue
        lo = np.minimum(a[differ], b[differ])
        hi = np.maximum(a[differ], b[differ])
        keys, counts = np.unique(np.stack([lo, hi], axis=1), axis=0, return_counts=True)
        for (x, y), n in zip(keys, counts):
            key = (int(x), int(y))
            pairs[key] = pairs.get(key, 0) + int(n)
    return pairs


def merge_small(
    level_map: np.ndarray, opts: RegionOptions, weight: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Absorb undersized components into their best-matched neighbour.

    `weight` scales the minimum area per pixel, so a background can be held to
    a much coarser standard than the subject. Returns (region_map,
    region_levels): an int32 map of dense region ids and each id's tone level.
    """
    cleaned = _despeckle(level_map, opts.speckle_filter)
    cleaned = _majority_smooth(cleaned, opts.smooth_radius, opts.smooth_iters)
    comp = measure.label(cleaned, connectivity=1).astype(np.int64)
    n = int(comp.max()) + 1

    size = np.bincount(comp.ravel(), minlength=n).astype(np.float64)
    level = np.zeros(n, dtype=np.int64)
    level[comp.ravel()] = cleaned.ravel()  # every component is level-uniform

    # Each component's area budget is min_area times its mean weight, so a
    # region straddling the subject edge gets an in-between threshold.
    if weight is None:
        weight_sum = size.copy()
    else:
        weight_sum = np.bincount(comp.ravel(), weights=weight.ravel(), minlength=n)

    def threshold(i: int) -> float:
        return opts.min_area * (weight_sum[i] / max(size[i], 1.0))

    neighbours: list[dict[int, int]] = [dict() for _ in range(n)]
    for (a, b), shared in _adjacency(comp).items():
        neighbours[a][b] = shared
        neighbours[b][a] = shared

    parent = np.arange(n, dtype=np.int64)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = int(parent[i])
        return i

    # Smallest first: absorbing the worst offenders can make a neighbour large
    # enough to stop being a problem itself.
    heap = [(size[i], i) for i in range(1, n) if size[i] < threshold(i)]
    heapq.heapify(heap)
    alive = np.ones(n, dtype=bool)

    while heap:
        recorded, i = heapq.heappop(heap)
        root = find(i)
        if not alive[root] or root != i or recorded != size[root]:
            continue  # stale entry
        if size[root] >= threshold(root):
            continue

        # Nearest tone first, then longest shared border. Merging by border
        # length alone lets a background blob drag a subject region to its own
        # level and erase the edge between them.
        best, best_key = None, None
        for j in neighbours[root]:
            rj = find(j)
            if rj == root or not alive[rj]:
                continue
            key = (-abs(int(level[rj]) - int(level[root])), neighbours[root][j], size[rj])
            if best_key is None or key > best_key:
                best, best_key = rj, key
        if best is None:
            continue

        # The bigger constituent decides the merged region's tone.
        keep_level = level[best] if size[best] >= size[root] else level[root]
        parent[root] = best
        alive[root] = False
        size[best] += size[root]
        weight_sum[best] += weight_sum[root]
        level[best] = keep_level
        merged = neighbours[best]
        for j, shared in neighbours[root].items():
            rj = find(j)
            if rj == best:
                continue
            merged[rj] = merged.get(rj, 0) + shared
        neighbours[root] = {}
        if size[best] < threshold(best):
            heapq.heappush(heap, (size[best], int(best)))

    roots = np.array([find(i) for i in range(n)], dtype=np.int64)
    uniq, dense = np.unique(roots, return_inverse=True)
    region_map = dense[comp].astype(np.int32)
    region_levels = level[uniq].astype(np.int16)
    return region_map, region_levels


def _outlines(mask: np.ndarray, offset: tuple[int, int], simplify: float) -> list[np.ndarray]:
    """Trace a boolean mask into simplified closed polygons in (x, y) order."""
    padded = np.pad(mask.astype(np.float32), 1)
    polys = []
    for contour in measure.find_contours(padded, 0.5):
        if simplify > 0:
            contour = measure.approximate_polygon(contour, tolerance=simplify)
        if len(contour) < 3:
            continue
        xy = np.empty_like(contour)
        xy[:, 0] = contour[:, 1] - 1 + offset[1]  # x <- column
        xy[:, 1] = contour[:, 0] - 1 + offset[0]  # y <- row
        polys.append(xy)
    return polys


def build_regions(
    region_map: np.ndarray, region_levels: np.ndarray, opts: RegionOptions
) -> list[Region]:
    """Extract outlines and number placement for every region."""
    slices = ndimage.find_objects(region_map + 1)
    regions: list[Region] = []
    for rid, sl in enumerate(slices):
        if sl is None:
            continue
        local = region_map[sl] == rid
        area = int(local.sum())
        if area == 0:
            continue

        # Furthest-from-edge point: the roomiest spot for a numeral. Padding
        # makes the transform measure distance to the region's own border even
        # where that border runs along the image edge.
        dist = ndimage.distance_transform_edt(np.pad(local, 1))
        flat = int(dist.argmax())
        row, col = np.unravel_index(flat, dist.shape)
        regions.append(
            Region(
                level=int(region_levels[rid]),
                area=area,
                outlines=_outlines(local, (sl[0].start, sl[1].start), opts.simplify),
                label_xy=(
                    float(col - 1 + sl[1].start),
                    float(row - 1 + sl[0].start),
                ),
                label_radius=float(dist[row, col]),
            )
        )
    return regions
