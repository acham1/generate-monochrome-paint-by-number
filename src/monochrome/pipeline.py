"""Photo in, paint-by-numbers template out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import regions as regions_mod
from . import render, tone


@dataclass
class Result:
    source: Path
    outputs: list[Path]
    region_count: int
    levels: int


def convert(
    source: Path,
    out_dir: Path,
    tone_opts: tone.ToneOptions,
    region_opts: regions_mod.RegionOptions,
    page: str = "letter",
    crop=None,
    subject=None,
    write_png: bool = True,
    write_lines: bool = False,
    write_svg: bool = True,
    write_pdf: bool = True,
) -> Result:
    gray = tone.load_gray(source, tone_opts.working_px, crop)
    prepared = tone.prepare(gray, tone_opts)
    level_map, level_values = tone.quantize(prepared, tone_opts)

    weight = regions_mod.subject_weight(
        level_map.shape, subject, region_opts.background_boost, region_opts.feather
    )
    region_map, region_levels = regions_mod.merge_small(level_map, region_opts, weight)
    found = regions_mod.build_regions(region_map, region_levels, region_opts)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    title = f"{stem}  ·  {tone_opts.levels} tones  ·  {len(found)} regions"
    outputs: list[Path] = []

    if write_png:
        path = out_dir / f"{stem}-mono.png"
        render.write_mono_png(region_map, region_levels, level_values, path)
        outputs.append(path)
    if write_lines:
        path = out_dir / f"{stem}-lines.png"
        render.write_lines_png(found, region_map.shape, path)
        outputs.append(path)
    if write_svg:
        path = out_dir / f"{stem}-pbn.svg"
        render.write_svg(found, region_levels, level_values, region_map.shape, title, path, page)
        outputs.append(path)
    if write_pdf:
        path = out_dir / f"{stem}-pbn.pdf"
        render.write_pdf(found, region_levels, level_values, region_map.shape, title, path, page)
        outputs.append(path)

    return Result(source=source, outputs=outputs, region_count=len(found), levels=tone_opts.levels)
