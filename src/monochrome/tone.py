"""Grayscale conversion and tonal quantization.

The paint-by-numbers pipeline starts here: a photograph becomes a small set of
flat gray levels. Everything downstream (regions, outlines, numbers) is derived
from the integer level map this module produces.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps
from skimage import exposure, restoration


@dataclass(frozen=True)
class ToneOptions:
    """Knobs for turning a photo into flat gray levels."""

    levels: int = 6
    working_px: int = 1000
    """Longest edge of the working image. Smaller -> fewer, chunkier regions."""
    smooth: float = 1.0
    """Edge-preserving denoise strength. 0 disables it."""
    contrast: float = 0.0
    """CLAHE clip limit. Off by default: local contrast flattens a dark subject
    into a bright background, which is exactly the separation a template needs.
    Raise it (0.003-0.01) only for flat, low-contrast originals."""
    mode: str = "kmeans"
    """How level boundaries are chosen: kmeans | quantile | uniform."""


def parse_crop(spec: str | None) -> tuple[float, float, float, float] | None:
    """Parse "left,top,right,bottom" fractions into a crop box."""
    if not spec:
        return None
    parts = [float(x) for x in spec.split(",")]
    if len(parts) != 4:
        raise ValueError("crop needs four comma-separated fractions: left,top,right,bottom")
    left, top, right, bottom = parts
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("crop fractions must satisfy 0 <= left < right <= 1 (same for top/bottom)")
    return left, top, right, bottom


def load_gray(path, working_px: int, crop=None) -> np.ndarray:
    """Load an image as a float32 grayscale array in [0, 1], cropped and downscaled."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("L")
        if crop:
            left, top, right, bottom = crop
            im = im.crop(
                (
                    round(left * im.width),
                    round(top * im.height),
                    round(right * im.width),
                    round(bottom * im.height),
                )
            )
        long_edge = max(im.size)
        if working_px and long_edge > working_px:
            scale = working_px / long_edge
            size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
            im = im.resize(size, Image.LANCZOS)
        return np.asarray(im, dtype=np.float32) / 255.0


def prepare(gray: np.ndarray, opts: ToneOptions) -> np.ndarray:
    """Apply local contrast and edge-preserving smoothing before quantizing."""
    out = gray
    if opts.contrast > 0:
        out = exposure.equalize_adapthist(out, clip_limit=opts.contrast)
    if opts.smooth > 0:
        # Bilateral keeps the edges we want to trace while flattening skin,
        # fabric and foliage noise that would otherwise explode the region count.
        out = restoration.denoise_bilateral(
            out.astype(np.float64),
            sigma_color=0.08 * opts.smooth,
            sigma_spatial=3.0 * opts.smooth,
            channel_axis=None,
        )
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def _kmeans_1d(values: np.ndarray, k: int, iters: int = 60) -> np.ndarray:
    """Lloyd's algorithm on a 256-bin histogram. Deterministic, quantile-seeded."""
    counts = np.bincount((values.ravel() * 255).astype(np.uint8), minlength=256)
    bins = np.arange(256, dtype=np.float64) / 255.0
    nz = counts > 0
    bins, counts = bins[nz], counts[nz].astype(np.float64)

    centers = np.quantile(values, np.linspace(0.5 / k, 1 - 0.5 / k, k))
    for _ in range(iters):
        assign = np.abs(bins[:, None] - centers[None, :]).argmin(axis=1)
        new = centers.copy()
        for j in range(k):
            m = assign == j
            if counts[m].sum() > 0:
                new[j] = (bins[m] * counts[m]).sum() / counts[m].sum()
        if np.allclose(new, centers, atol=1e-5):
            centers = new
            break
        centers = new
    return np.sort(centers)


def quantize(gray: np.ndarray, opts: ToneOptions) -> tuple[np.ndarray, np.ndarray]:
    """Split a grayscale image into `levels` flat tones.

    Returns (level_map, level_values) where level_map holds indices 0..levels-1
    ordered darkest to lightest, and level_values holds the gray value in [0, 1]
    each index should be painted.
    """
    k = opts.levels
    if k < 2:
        raise ValueError("levels must be at least 2")

    if opts.mode == "uniform":
        centers = (np.arange(k) + 0.5) / k
    elif opts.mode == "quantile":
        centers = np.quantile(gray, np.linspace(0.5 / k, 1 - 0.5 / k, k))
    elif opts.mode == "kmeans":
        centers = _kmeans_1d(gray, k)
    else:
        raise ValueError(f"unknown level mode: {opts.mode!r}")

    edges = (centers[:-1] + centers[1:]) / 2.0
    level_map = np.digitize(gray, edges).astype(np.int16)
    return level_map, centers.astype(np.float32)
