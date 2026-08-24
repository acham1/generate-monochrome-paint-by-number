"""Grayscale conversion and tonal quantization.

The paint-by-numbers pipeline starts here: a photograph becomes a small set of
flat gray levels. Everything downstream (regions, outlines, numbers) is derived
from the integer level map this module produces.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps
from skimage import color, exposure, restoration


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


def load_lightness(path, working_px: int, crop=None) -> np.ndarray:
    """Load an image as CIE L* lightness in [0, 1], cropped and downscaled.

    Quantizing plain luma would put the tone boundaries in the wrong places: it
    is gamma-encoded, so equal numeric steps are not equal *visual* steps, and a
    painter mixing six evenly-spaced grays is working in perceptual terms. L* is
    built for exactly that, so every stage downstream operates in it.
    """
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
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
        rgb = np.asarray(im, dtype=np.float64) / 255.0
    return (color.rgb2lab(rgb)[..., 0] / 100.0).astype(np.float32)


def lightness_to_srgb(lightness) -> np.ndarray:
    """CIE L* in [0, 1] back to an sRGB gray in [0, 1], for display and print.

    The pipeline reasons in L* but screens and printers want sRGB, so the two
    are kept apart: L* decides where tones fall, this decides how they look.
    """
    values = np.asarray(lightness, dtype=np.float64)
    flat = values.reshape(-1)
    lab = np.stack([flat * 100.0, np.zeros_like(flat), np.zeros_like(flat)], axis=-1)
    rgb = color.lab2rgb(lab.reshape(-1, 1, 3))
    return np.clip(rgb[:, 0, 0], 0.0, 1.0).reshape(values.shape).astype(np.float32)


def prepare(gray: np.ndarray, opts: ToneOptions) -> np.ndarray:
    """Apply local contrast and edge-preserving smoothing before quantizing.

    Operates on L*, so the denoise radius is in perceptual units too.
    """
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
