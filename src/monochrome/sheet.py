"""Contact sheets for reviewing a whole batch at once.

Opening sixteen PDFs to see whether a setting helped is tedious; one tiled
image answers it immediately.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw

from .render import _load_font

KINDS = {"mono": "-mono.png", "lines": "-lines.png"}
LABEL_H = 26


def _region_count(image_path: Path) -> str | None:
    """Read the region count out of the sibling SVG's title, if one exists."""
    stem = image_path.name
    for suffix in KINDS.values():
        stem = stem.replace(suffix, "")
    svg = image_path.with_name(f"{stem}-pbn.svg")
    if not svg.exists():
        return None
    match = re.search(r"(\d+) regions", svg.read_text(errors="ignore")[:600])
    return match.group(1) if match else None


def build(
    directory: Path,
    kind: str,
    out_path: Path,
    cols: int = 4,
    cell: int = 460,
    prefix: str = "",
) -> tuple[Path, int]:
    """Tile every `kind` image in `directory` into one labelled sheet."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    files = sorted(directory.glob(f"*{KINDS[kind]}"))
    if not files:
        raise FileNotFoundError(f"no {kind} images in {directory}")

    cols = max(1, min(cols, len(files)))
    rows = (len(files) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + LABEL_H)), "white")
    draw = ImageDraw.Draw(sheet)
    font = _load_font(15)

    for i, path in enumerate(files):
        name = path.name.replace(KINDS[kind], "")
        if prefix and name.startswith(prefix):
            name = name[len(prefix) :]
        count = _region_count(path)
        label = f"{name}   {count} regions" if count else name

        with Image.open(path) as im:
            tile = im.convert("RGB")
            tile.thumbnail((cell - 12, cell - 12))
        x = (i % cols) * cell
        y = (i // cols) * (cell + LABEL_H)
        draw.text((x + 8, y + 5), label, fill="black", font=font)
        sheet.paste(tile, (x + (cell - tile.width) // 2, y + LABEL_H))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=90)
    return out_path, len(files)
