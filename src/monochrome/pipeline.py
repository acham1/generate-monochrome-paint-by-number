"""Photo in, paint-by-numbers template out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import mesh as mesh_mod
from . import regions as regions_mod
from . import render, tone
from .mesh import MeshOptions


@dataclass
class Result:
    source: Path
    outputs: list[Path]
    region_count: int
    levels: int
    mesh_info: dict | None = None


def convert(
    source: Path,
    out_dir: Path,
    tone_opts: tone.ToneOptions,
    region_opts: regions_mod.RegionOptions,
    page: str = "letter",
    label: str = "",
    crop=None,
    subject=None,
    write_png: bool = True,
    write_lines: bool = False,
    write_svg: bool = True,
    write_pdf: bool = True,
    write_stl: bool = False,
    mesh_opts: MeshOptions | None = None,
) -> Result:
    lightness = tone.load_lightness(source, tone_opts.working_px, crop)
    prepared = tone.prepare(lightness, tone_opts)
    level_map, level_values = tone.quantize(prepared, tone_opts)
    paint_values = tone.paint_palette(level_values, tone_opts.palette)
    display_grays = tone.lightness_to_srgb(paint_values)

    weight = regions_mod.subject_weight(
        level_map.shape, subject, region_opts.background_boost, region_opts.feather
    )
    region_map, region_levels = regions_mod.merge_small(level_map, region_opts, weight)
    found = regions_mod.build_regions(region_map, region_levels, region_opts)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    # The filename stays off the page: these get printed and handed to someone,
    # and the source photo is often the surprise.
    parts = [label, f"{tone_opts.levels} tones", f"{len(found)} regions"]
    title = "  ·  ".join(part for part in parts if part)
    outputs: list[Path] = []

    if write_png:
        path = out_dir / f"{stem}-mono.png"
        render.write_mono_png(region_map, region_levels, display_grays, path)
        outputs.append(path)
    if write_lines:
        path = out_dir / f"{stem}-lines.png"
        render.write_lines_png(found, region_map.shape, path)
        outputs.append(path)
    if write_svg:
        path = out_dir / f"{stem}-pbn.svg"
        render.write_svg(found, display_grays, paint_values, region_map.shape, title, path, page)
        outputs.append(path)
    if write_pdf:
        path = out_dir / f"{stem}-pbn.pdf"
        render.write_pdf(found, display_grays, paint_values, region_map.shape, title, path, page)
        outputs.append(path)

    mesh_info = None
    if write_stl:
        path = out_dir / f"{stem}-relief.stl"
        mesh_info = mesh_mod.write_relief_stl(
            region_map,
            region_levels,
            paint_values,
            mesh_opts or MeshOptions(),
            path,
            narrowest_px=min((r.label_radius for r in found), default=None),
        )
        outputs.append(path)

    return Result(
        source=source,
        outputs=outputs,
        region_count=len(found),
        levels=tone_opts.levels,
        mesh_info=mesh_info,
    )
