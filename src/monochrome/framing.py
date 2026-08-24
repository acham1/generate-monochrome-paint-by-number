"""Framing: loading the manifest, and checking it against the photographs.

A crop or subject box that quietly clips someone out of the picture is the
easiest mistake to make here and the hardest to see in a finished template, so
the framing gets its own overlay sheet for review.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .render import _load_font
from .tone import parse_crop

Box = tuple[float, float, float, float]


def load(path: Path) -> dict[str, dict]:
    """Read a framing manifest, normalising the shorthand list form."""
    raw = json.loads(Path(path).read_text())
    table: dict[str, dict] = {}
    for name, entry in raw.items():
        if isinstance(entry, dict):
            table[name] = entry
        else:  # bare [l,t,r,b] means subject only
            table[name] = {"subject": entry}
    return table


def entry_boxes(entry: dict) -> tuple[Box | None, Box | None]:
    """Parse and validate one manifest entry into (crop, subject)."""
    crop = entry.get("crop")
    subject = entry.get("subject")
    to_box = lambda v: parse_crop(",".join(str(x) for x in v)) if v is not None else None
    return to_box(crop), to_box(subject)


def coverage(subject: Box | None) -> float:
    """Fraction of the (already cropped) frame a subject box covers."""
    if subject is None:
        return 1.0
    left, top, right, bottom = subject
    return (right - left) * (bottom - top)


def check_sheet(
    sources: list[Path],
    table: dict[str, dict],
    out_path: Path,
    cols: int = 3,
    tile: int = 540,
) -> tuple[Path, list[str]]:
    """Draw each photo as it will be framed, with its subject box outlined.

    Returns the sheet path and a list of warnings for boxes so large they
    suppress nothing.
    """
    font = _load_font(17)
    warnings: list[str] = []
    tiles = []

    for source in sources:
        entry = table.get(source.name, {})
        crop, subject = entry_boxes(entry)
        with Image.open(source) as opened:
            im = ImageOps.exif_transpose(opened).convert("RGB")
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
        if subject:
            left, top, right, bottom = subject
            ImageDraw.Draw(im).rectangle(
                [left * im.width, top * im.height, right * im.width - 1, bottom * im.height - 1],
                outline=(0, 255, 0),
                width=max(6, im.width // 140),
            )
            if coverage(subject) > 0.9:
                warnings.append(
                    f"{source.name}: subject box covers "
                    f"{coverage(subject):.0%} of the frame, so it suppresses almost nothing"
                )
        im.thumbnail((tile, tile))
        note = "cropped" if crop else "full frame"
        tiles.append((f"{source.stem}  ({note})", im))

    cols = max(1, min(cols, len(tiles)))
    rows = (len(tiles) + cols - 1) // cols
    cw = max(t.width for _, t in tiles)
    ch = max(t.height for _, t in tiles)
    sheet = Image.new("RGB", (cols * (cw + 12) + 12, rows * (ch + 26) + 12), "#f4f4f4")
    draw = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(tiles):
        x = 12 + (i % cols) * (cw + 12)
        y = 12 + (i // cols) * (ch + 26)
        draw.text((x, y + 3), label, fill="black", font=font)
        sheet.paste(im, (x, y + 24))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=93)
    return out_path, warnings
