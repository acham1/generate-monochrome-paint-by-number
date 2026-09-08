"""Command line entry point."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import typer

from .pipeline import convert
from .regions import RegionOptions
from . import framing as framing_mod
from . import mesh as mesh_mod
from . import sheet as sheet_mod
from .render import PAGE_SIZES
from .mesh import LithophaneOptions, MeshOptions
from .tone import ToneOptions, parse_crop

app = typer.Typer(add_completion=False, help=__doc__)

# Defaults live on the option dataclasses; the CLI mirrors them so the two
# cannot drift apart.
TONE_DEFAULTS = ToneOptions()
REGION_DEFAULTS = RegionOptions()
MESH_DEFAULTS = MeshOptions()
LITHO_DEFAULTS = LithophaneOptions()

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


def _label(source: Path, index: int, mode: str) -> str:
    """Identify a template on the page without naming the source photo."""
    if mode == "none":
        return ""
    if mode == "name":
        return source.stem
    digits = re.findall(r"\d+", source.stem)
    return digits[-1] if digits else str(index + 1)


def _sources(paths: list[Path]) -> list[Path]:
    """Expand a mix of files and directories into a de-duplicated image list."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            inside = sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
            if not inside:
                raise typer.BadParameter(f"no images found in {path}")
            found.extend(inside)
        else:
            found.append(path)
    seen, unique = set(), []
    for path in found:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


@app.callback()
def main() -> None:
    """Turn photographs into monochrome paint-by-numbers templates."""


@app.command()
def pbn(
    paths: list[Path] = typer.Argument(
        ..., exists=True, help="Image files and/or directories of images."
    ),
    out: Path = typer.Option(Path("out"), "--out", "-o", help="Output directory."),
    levels: int = typer.Option(
        TONE_DEFAULTS.levels, "--levels", "-l", min=2, max=12, help="Number of gray tones."
    ),
    min_region: int = typer.Option(
        REGION_DEFAULTS.min_area,
        "--min-region",
        help="Absorb regions smaller than this many working pixels.",
    ),
    working_px: int = typer.Option(
        TONE_DEFAULTS.working_px,
        "--working-px",
        help="Longest edge used for region detection. Lower = chunkier.",
    ),
    simplify: float = typer.Option(
        REGION_DEFAULTS.simplify, "--simplify", help="Outline simplification, in pixels."
    ),
    smooth_radius: float = typer.Option(
        REGION_DEFAULTS.smooth_radius,
        "--smooth-radius",
        help="Majority-filter radius that rounds off ragged regions.",
    ),
    smooth_iters: int = typer.Option(
        REGION_DEFAULTS.smooth_iters, "--smooth-iters", help="Majority-filter passes."
    ),
    smooth: float = typer.Option(
        TONE_DEFAULTS.smooth, "--smooth", help="Edge-preserving denoise strength."
    ),
    contrast: float = typer.Option(
        TONE_DEFAULTS.contrast,
        "--contrast",
        help="Local contrast (CLAHE) clip limit. 0 = off; try 0.003 if flat.",
    ),
    mode: str = typer.Option(
        TONE_DEFAULTS.mode,
        "--mode",
        help="Tone split: kmeans | quantile | uniform.",
    ),
    palette: str = typer.Option(
        "fitted",
        "--palette",
        help="What the tones are painted: fitted (the tone each region was "
        "measured at) or ramp (even steps black to white). Unlike --mode this "
        "does not move any region; it only changes the grays poured into them.",
    ),
    stl_max: float = typer.Option(
        MESH_DEFAULTS.max_mm,
        "--stl-max",
        help="Longest edge of the printed relief in mm. The other follows the "
        "image's aspect, so one figure fits either orientation to the same bed.",
    ),
    stl_relief: float = typer.Option(
        MESH_DEFAULTS.relief_mm,
        "--stl-relief",
        help="Millimetres climbed from the darkest tone to the lightest.",
    ),
    stl_base: float = typer.Option(
        MESH_DEFAULTS.base_mm, "--stl-base", help="Solid slab under the darkest tone, in mm."
    ),
    stl_nozzle: float = typer.Option(
        MESH_DEFAULTS.nozzle_mm,
        "--stl-nozzle",
        help="Nozzle width in mm. Sets the sampling pitch at one column per bead, "
        "so detail tracks the printed size automatically.",
    ),
    litho_thin: float = typer.Option(
        LITHO_DEFAULTS.thin_mm,
        "--litho-thin",
        help="Thickness under the lightest tone, in mm.",
    ),
    litho_thick: float = typer.Option(
        LITHO_DEFAULTS.thick_mm,
        "--litho-thick",
        help="Thickness under the darkest tone, in mm. Keep the whole plate thin: "
        "contrast comes from the ratio of thickest to thinnest.",
    ),
    litho_layer: float = typer.Option(
        LITHO_DEFAULTS.layer_mm,
        "--litho-layer",
        help="Layer height to snap thicknesses to. Printed flat, a tone's thickness "
        "is its layer count, so unsnapped values round unpredictably.",
    ),
    litho_gamma: float = typer.Option(
        LITHO_DEFAULTS.gamma,
        "--litho-gamma",
        help="Shapes tone to thickness. Above 1 thins the midtones and brightens "
        "them. Depends on your filament; calibrate with 'monochrome litho-test'.",
    ),
    litho_border: float = typer.Option(
        LITHO_DEFAULTS.border_mm,
        "--litho-border",
        help="Width of a solid opaque frame in mm. Reads black and stiffens the plate.",
    ),
    stl_seam: float = typer.Option(
        MESH_DEFAULTS.seam_mm,
        "--stl-seam",
        help="Width in mm of a raised seam tracing every region boundary. Sharpens "
        "the edges without flattening the tonal steps. 0 leaves them bare.",
    ),
    stl_seam_rise: float = typer.Option(
        MESH_DEFAULTS.seam_rise_mm,
        "--stl-seam-rise",
        help="How far the seam stands above the higher plateau it divides.",
    ),
    stl_border: float = typer.Option(
        MESH_DEFAULTS.border_mm,
        "--stl-border",
        help="Width in mm of a raised frame around the relief. 0 leaves it bare.",
    ),
    stl_border_rise: float = typer.Option(
        MESH_DEFAULTS.border_rise_mm,
        "--stl-border-rise",
        help="How far the frame stands above the lightest tone.",
    ),
    stl_border_gap: float = typer.Option(
        MESH_DEFAULTS.border_gap_mm,
        "--stl-border-gap",
        help="Recessed gutter in mm between frame and picture.",
    ),
    stl_invert: bool = typer.Option(
        False, "--stl-invert", help="Raise the dark tones instead, for a backlit piece."
    ),
    page: str = typer.Option("letter", "--page", help="Page size: letter | a4."),
    label_from: str = typer.Option(
        "number",
        "--label-from",
        help="What identifies a template on the page: number (trailing digits in "
        "the filename), name (the full stem), or none. Defaults to number so a "
        "printed template does not give away the source photo.",
    ),
    subject: Optional[str] = typer.Option(
        None,
        "--subject",
        help="Where the people are, as left,top,right,bottom fractions "
        "(e.g. 0.3,0.2,0.8,1.0). Detail is kept inside; the background outside "
        "collapses into large flat shapes.",
    ),
    background_boost: float = typer.Option(
        REGION_DEFAULTS.background_boost,
        "--background-boost",
        help="How much coarser the area outside --subject is. 1 = no difference.",
    ),
    framing: Optional[Path] = typer.Option(
        None,
        "--framing",
        exists=True,
        help="JSON file of per-photo framing, so a whole folder renders in one "
        'command. Map each filename to {"crop": [...], "subject": [...]}, or to a '
        "bare [left,top,right,bottom] list to set just the subject box.",
    ),
    feather: float = typer.Option(
        REGION_DEFAULTS.feather,
        "--feather",
        help="Softness of the --subject edge, as a fraction of the long edge. "
        "Too small and the box shows as a straight line.",
    ),
    no_crop: bool = typer.Option(
        False,
        "--no-crop",
        help="Ignore every crop, from the manifest and from --crop alike, and render "
        "the full frame. Subject boxes still apply.",
    ),
    crop: Optional[str] = typer.Option(
        None,
        "--crop",
        help="Crop to fractions of the frame as left,top,right,bottom "
        "(e.g. 0.15,0.1,0.85,1.0) to cut away background.",
    ),
    formats: str = typer.Option(
        "png,lines,svg,pdf",
        "--formats",
        help="Comma-separated outputs: png (flat monochrome), lines (rasterized "
        "outlines for on-screen review), svg, pdf, stl (a 3-D relief where "
        "brightness becomes height), litho (a thin plate read by light through "
        "it, where brightness becomes thinness).",
    ),
) -> None:
    """Convert photos to monochrome and emit printable paint-by-numbers templates."""
    if page not in PAGE_SIZES:
        raise typer.BadParameter(f"page must be one of {sorted(PAGE_SIZES)}")
    if mode not in {"kmeans", "quantile", "uniform"}:
        raise typer.BadParameter("mode must be kmeans, quantile or uniform")
    wanted = {f.strip().lower() for f in formats.split(",") if f.strip()}
    unknown = wanted - {"png", "lines", "svg", "pdf", "stl", "litho"}
    if unknown:
        raise typer.BadParameter(f"unknown formats: {sorted(unknown)}")

    try:
        crop_box = parse_crop(crop)
        subject_box = parse_crop(subject)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if palette not in {"fitted", "ramp"}:
        raise typer.BadParameter("palette must be fitted or ramp")

    litho_opts = LithophaneOptions(
        max_mm=stl_max,
        nozzle_mm=stl_nozzle,
        thin_mm=litho_thin,
        thick_mm=litho_thick,
        layer_mm=litho_layer,
        gamma=litho_gamma,
        border_mm=litho_border,
    )

    mesh_opts = MeshOptions(
        max_mm=stl_max,
        relief_mm=stl_relief,
        base_mm=stl_base,
        nozzle_mm=stl_nozzle,
        invert=stl_invert,
        seam_mm=stl_seam,
        seam_rise_mm=stl_seam_rise,
        border_mm=stl_border,
        border_rise_mm=stl_border_rise,
        border_gap_mm=stl_border_gap,
    )

    tone_opts = ToneOptions(
        levels=levels,
        working_px=working_px,
        smooth=smooth,
        contrast=contrast,
        mode=mode,
        palette=palette,
    )
    region_opts = RegionOptions(
        min_area=min_region,
        simplify=simplify,
        smooth_radius=smooth_radius,
        smooth_iters=smooth_iters,
        background_boost=background_boost,
        feather=feather,
    )

    if label_from not in {"number", "name", "none"}:
        raise typer.BadParameter("label-from must be number, name or none")

    sources = _sources(paths)
    table: dict[str, dict] = {}
    if framing:
        table = framing_mod.load(framing)
        unknown_files = sorted(set(table) - {p.name for p in sources})
        if unknown_files:
            typer.echo(f"note: framing entries not among the inputs: {unknown_files}")

    for index, source in enumerate(sources):
        # A manifest entry wins over the single-image flags, so one command can
        # render a whole folder with per-photo framing.
        entry_crop, entry_subject = framing_mod.entry_boxes(table.get(source.name, {}))
        box = entry_subject or subject_box
        source_crop = None if no_crop else (entry_crop or crop_box)
        if box is not None and framing_mod.coverage(box) > 0.9 and background_boost > 1:
            typer.echo(
                f"note: {source.name} subject box covers "
                f"{framing_mod.coverage(box):.0%} of the frame - little will be suppressed"
            )

        typer.echo(f"{source.name} ... ", nl=False)
        result = convert(
            source,
            out,
            tone_opts,
            region_opts,
            page=page,
            label=_label(source, index, label_from),
            crop=source_crop,
            subject=box,
            write_png="png" in wanted,
            write_lines="lines" in wanted,
            write_svg="svg" in wanted,
            write_pdf="pdf" in wanted,
            write_stl="stl" in wanted,
            mesh_opts=mesh_opts,
            write_litho="litho" in wanted,
            litho_opts=litho_opts,
        )
        typer.echo(f"{result.region_count} regions")
        for output in result.outputs:
            typer.echo(f"    {output}")
        if result.litho_info:
            litho = result.litho_info
            layers = litho.get("layers")
            shape = f"{litho['width_mm']:.0f} x {litho['depth_mm']:.0f} mm"
            thin, thick = litho["thickness_mm"][-1], litho["thickness_mm"][0]
            typer.echo(f"    litho {shape}, {thin:.1f}-{thick:.1f} mm thick")
            if layers:
                typer.echo(f"    layers per tone, dark to light: {layers}")
            if litho["distinct_thicknesses"] < len(litho["thickness_mm"]):
                typer.echo(
                    "    warning: two tones snapped to the same thickness and will "
                    "print identically. Widen --litho-thin/--litho-thick, or use a "
                    "finer --litho-layer."
                )
        if result.mesh_info:
            info = result.mesh_info
            typer.echo(
                f"    relief {info['width_mm']:.0f} x {info['depth_mm']:.0f} x "
                f"{info['height_mm']:.1f} mm, {info['triangles']:,} triangles"
            )
            ratio = info.get("wall_ratio")  # noqa: F841 - used below
            if ratio is not None and ratio > mesh_mod.WALL_RATIO_WARN:
                typer.echo(
                    f"    warning: {info['step_mm']:.1f}mm steps against a "
                    f"{info['narrowest_mm']:.1f}mm plateau is {ratio:.0f}:1 - thin "
                    f"walls may not print. Lower --stl-relief or raise --min-region."
                )


@app.command("litho-test")
def litho_test(
    out: Path = typer.Option(
        Path("litho-test.stl"), "--out", "-o", help="Where to write the strip."
    ),
    levels: int = typer.Option(
        TONE_DEFAULTS.levels, "--levels", "-l", min=2, max=24, help="How many tones."
    ),
    thin: float = typer.Option(LITHO_DEFAULTS.thin_mm, "--litho-thin"),
    thick: float = typer.Option(LITHO_DEFAULTS.thick_mm, "--litho-thick"),
    layer: float = typer.Option(LITHO_DEFAULTS.layer_mm, "--litho-layer"),
    gamma: float = typer.Option(LITHO_DEFAULTS.gamma, "--litho-gamma"),
    nozzle: float = typer.Option(LITHO_DEFAULTS.nozzle_mm, "--stl-nozzle"),
    patch: float = typer.Option(20.0, "--patch", help="Size of each patch in mm."),
) -> None:
    """Emit a stepped test strip for calibrating --litho-gamma to your filament.

    Print it, hold it to a light, and look at the spacing of the steps. If the
    middle patches read darker than an even ladder, raise --litho-gamma and try
    again. No amount of geometry can predict this: it depends on how much your
    particular filament attenuates.
    """
    opts = LithophaneOptions(
        nozzle_mm=nozzle, thin_mm=thin, thick_mm=thick, layer_mm=layer, gamma=gamma
    )
    info = mesh_mod.write_lithophane_test_strip(levels, opts, out, patch_mm=patch)
    typer.echo(f"{out}  ({info['triangles']:,} triangles)")
    typer.echo(f"  {info['width_mm']:.0f} x {info['depth_mm']:.0f} mm, {levels} patches")
    typer.echo(f"  thickness, darkest to lightest: {info['thickness_mm']} mm")
    typer.echo(f"  laid out {info['order']}")


@app.command()
def check(
    paths: list[Path] = typer.Argument(
        ..., exists=True, help="Image files and/or directories of images."
    ),
    framing: Path = typer.Option(..., "--framing", exists=True, help="Framing manifest to check."),
    out: Path = typer.Option(
        Path("framing-check.jpg"), "--out", "-o", help="Where to write the overlay sheet."
    ),
    cols: int = typer.Option(3, "--cols", min=1, help="Tiles per row."),
    tile: int = typer.Option(540, "--tile", min=120, help="Tile size in pixels."),
) -> None:
    """Draw each photo as it will be framed, so you can see if anyone is clipped.

    A crop that slices through one of two people produces a plausible-looking
    template of the wrong picture, which is why this exists.
    """
    sources = _sources(paths)
    table = framing_mod.load(framing)
    unknown = sorted(set(table) - {p.name for p in sources})
    if unknown:
        typer.echo(f"note: framing entries not among the inputs: {unknown}")
    path, warnings = framing_mod.check_sheet(sources, table, out, cols, tile)
    for warning in warnings:
        typer.echo(f"warning: {warning}")
    typer.echo(f"{path}  ({len(sources)} tiles)")


@app.command()
def sheet(
    directory: Path = typer.Argument(
        ..., exists=True, file_okay=False, help="Directory of generated outputs."
    ),
    kind: str = typer.Option(
        "lines", "--kind", help="Which images to tile: lines | mono. 'all' does both."
    ),
    out: Optional[Path] = typer.Option(
        None, "--out", "-o", help="Output image. Defaults to proof-<kind>.jpg in DIRECTORY."
    ),
    cols: int = typer.Option(4, "--cols", min=1, help="Tiles per row."),
    cell: int = typer.Option(460, "--cell", min=80, help="Tile size in pixels."),
    prefix: str = typer.Option(
        "", "--strip-prefix", help="Drop this leading text from tile labels."
    ),
) -> None:
    """Tile a batch of generated images into one proof sheet for review."""
    kinds = sorted(sheet_mod.KINDS) if kind == "all" else [kind]
    for one in kinds:
        if one not in sheet_mod.KINDS:
            raise typer.BadParameter(f"kind must be one of {sorted(sheet_mod.KINDS)} or 'all'")
    for one in kinds:
        target = out if out and len(kinds) == 1 else directory / f"proof-{one}.jpg"
        try:
            path, count = sheet_mod.build(directory, one, target, cols, cell, prefix)
        except FileNotFoundError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"{path}  ({count} tiles)")


if __name__ == "__main__":
    app()
