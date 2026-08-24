"""Output writers: flat monochrome PNG, and printable SVG / PDF templates.

The template layout is shared between SVG and PDF: a titled outline drawing
scaled to fit the page, with a swatch key along the bottom mapping each numeral
to the gray it should be painted. SVG measures y downward and PDF upward, so
each writer converts through its own tiny transform.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4, letter
from reportlab.pdfgen import canvas as pdfcanvas

from .regions import Region

PAGE_SIZES = {"letter": letter, "a4": A4}
MARGIN = 36.0
TITLE_H = 20.0
KEY_H = 46.0
SWATCH = 20.0
SWATCH_GAP = 8.0
STROKE = 0.4
MIN_NUMERAL = 2.6
MAX_NUMERAL = 13.0
FONT = "Helvetica"


@dataclass(frozen=True)
class Layout:
    """Where the drawing sits on the page, in PDF-style points from top-left."""

    page_w: float
    page_h: float
    scale: float
    off_x: float
    off_y: float
    draw_h: float
    key_y: float
    """Top of the swatch key, in points from the top of the page."""


def plan_layout(img_shape: tuple[int, int], page: str, landscape_ok: bool = True) -> Layout:
    """Fit the drawing to the page, rotating the page to match the photo."""
    h, w = img_shape
    pw, ph = PAGE_SIZES[page]
    if landscape_ok and w > h:
        pw, ph = ph, pw

    avail_w = pw - 2 * MARGIN
    avail_h = ph - 2 * MARGIN - TITLE_H - KEY_H
    scale = min(avail_w / w, avail_h / h)
    # Centre the drawing in the space between title and key, and pin the key to
    # the bottom margin, so slack is shared rather than dumped below the image.
    return Layout(
        page_w=pw,
        page_h=ph,
        scale=scale,
        off_x=MARGIN + (avail_w - w * scale) / 2,
        off_y=MARGIN + TITLE_H + (avail_h - h * scale) / 2,
        draw_h=h * scale,
        key_y=ph - MARGIN - SWATCH,
    )


def numeral_size(region: Region, scale: float, text: str) -> float:
    """Pick a font size that fits inside the region's widest inscribed circle."""
    room = max(region.label_radius * scale, 0.0)
    size = min(room * 1.5, MAX_NUMERAL)
    # Helvetica digits run about 0.56em wide; keep the whole label inside.
    width_limited = (room * 2) / max(0.56 * len(text), 0.56)
    return max(min(size, width_limited), MIN_NUMERAL)


def write_mono_png(region_map: np.ndarray, region_levels, display_grays, path) -> None:
    """Save the flat monochrome rendering the template is derived from."""
    grays = (np.asarray(display_grays)[np.asarray(region_levels)] * 255).astype(np.uint8)
    Image.fromarray(grays[region_map]).save(path)


def _load_font(size: float):
    """A truetype face at `size` if the system has one, else PIL's built-in."""
    size = max(int(round(size)), 1)
    for candidate in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 cannot size the default font
        return ImageFont.load_default()


def write_lines_png(regions, img_shape, path, supersample: int = 3) -> None:
    """Rasterize just the drawing - outlines and numerals, no page furniture.

    Drawn oversized and reduced so the hairlines come out anti-aliased. Useful
    for on-screen review of a whole batch, where opening every PDF is tedious.
    """
    h, w = img_shape
    ss = max(int(supersample), 1)
    canvas = Image.new("L", (w * ss, h * ss), 255)
    draw = ImageDraw.Draw(canvas)

    for region in regions:
        for poly in region.outlines:
            pts = [(float(x) * ss, float(y) * ss) for x, y in poly]
            draw.line(pts + pts[:1], fill=0, width=max(ss, 1), joint="curve")

    for region in regions:
        text = str(region.level + 1)
        # Size the numeral in working pixels, then scale into the oversized
        # canvas. Passing `ss` as the scale would size it in page points and
        # leave it invisible.
        font = _load_font(numeral_size(region, 1.0, text) * ss)
        x, y = region.label_xy
        draw.text((x * ss, y * ss), text, fill=0, font=font, anchor="mm")

    canvas.resize((w, h), Image.LANCZOS).save(path)


def _key_entries(display_grays, lightness) -> list[tuple[str, float, int]]:
    """Numeral, swatch colour and perceptual lightness for each tone.

    The swatch is drawn in sRGB so it looks right, but the percentage quoted
    beside it is L*: how light the tone is to the eye, which is the number a
    painter can actually match against.
    """
    return [
        (str(i + 1), float(gray), round(float(light) * 100))
        for i, (gray, light) in enumerate(zip(display_grays, lightness))
    ]


def write_svg(regions, display_grays, lightness, img_shape, title, path, page="letter") -> None:
    lay = plan_layout(img_shape, page)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{lay.page_w:.1f}" '
        f'height="{lay.page_h:.1f}" viewBox="0 0 {lay.page_w:.1f} {lay.page_h:.1f}">',
        f'<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{MARGIN:.1f}" y="{MARGIN + 10:.1f}" font-family="{FONT}" '
        f'font-size="10" fill="#666">{_esc(title)}</text>',
        f'<g fill="none" stroke="#000000" stroke-width="{STROKE}" ' 'stroke-linejoin="round">',
    ]

    def px(x, y):
        return lay.off_x + x * lay.scale, lay.off_y + y * lay.scale

    for region in regions:
        for poly in region.outlines:
            pts = [px(x, y) for x, y in poly]
            d = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts) + " Z"
            parts.append(f'<path d="{d}"/>')
    parts.append("</g>")

    parts.append(f'<g font-family="{FONT}" fill="#000000" text-anchor="middle">')
    for region in regions:
        text = str(region.level + 1)
        size = numeral_size(region, lay.scale, text)
        x, y = px(*region.label_xy)
        parts.append(
            f'<text x="{x:.2f}" y="{y + size * 0.36:.2f}" font-size="{size:.2f}">{text}</text>'
        )
    parts.append("</g>")

    key_y = lay.key_y
    for i, (text, gray, pct) in enumerate(_key_entries(display_grays, lightness)):
        sx = MARGIN + i * (SWATCH + SWATCH_GAP + 22)
        hexv = "#%02x%02x%02x" % ((round(gray * 255),) * 3)
        fg = "#ffffff" if gray < 0.5 else "#000000"
        parts.append(
            f'<rect x="{sx:.1f}" y="{key_y:.1f}" width="{SWATCH}" height="{SWATCH}" '
            f'fill="{hexv}" stroke="#000" stroke-width="0.4"/>'
            f'<text x="{sx + SWATCH / 2:.1f}" y="{key_y + SWATCH / 2 + 3.4:.1f}" '
            f'font-family="{FONT}" font-size="9.5" fill="{fg}" text-anchor="middle">{text}</text>'
            f'<text x="{sx + SWATCH + 4:.1f}" y="{key_y + SWATCH / 2 + 3.2:.1f}" '
            f'font-family="{FONT}" font-size="7.5" fill="#333">{pct}%</text>'
        )
    parts.append("</svg>")
    with open(path, "w") as fh:
        fh.write("\n".join(parts))


def write_pdf(regions, display_grays, lightness, img_shape, title, path, page="letter") -> None:
    lay = plan_layout(img_shape, page)
    c = pdfcanvas.Canvas(str(path), pagesize=(lay.page_w, lay.page_h))
    c.setTitle(title)

    # Printable templates need a real white page, not transparency.
    c.setFillGray(1.0)
    c.rect(0, 0, lay.page_w, lay.page_h, stroke=0, fill=1)

    def px(x, y):
        """Working pixels -> PDF points, flipping to y-up."""
        return lay.off_x + x * lay.scale, lay.page_h - (lay.off_y + y * lay.scale)

    c.setFont(FONT, 10)
    c.setFillGray(0.4)
    c.drawString(MARGIN, lay.page_h - MARGIN - 2, title)

    c.setFillGray(0.0)
    c.setStrokeGray(0.0)
    c.setLineWidth(STROKE)
    c.setLineJoin(1)
    for region in regions:
        for poly in region.outlines:
            p = c.beginPath()
            first = True
            for x, y in poly:
                fx, fy = px(x, y)
                if first:
                    p.moveTo(fx, fy)
                    first = False
                else:
                    p.lineTo(fx, fy)
            p.close()
            c.drawPath(p, stroke=1, fill=0)

    for region in regions:
        text = str(region.level + 1)
        size = numeral_size(region, lay.scale, text)
        x, y = px(*region.label_xy)
        c.setFont(FONT, size)
        c.drawCentredString(x, y - size * 0.36, text)

    key_top = lay.page_h - lay.key_y
    for i, (text, gray, pct) in enumerate(_key_entries(display_grays, lightness)):
        sx = MARGIN + i * (SWATCH + SWATCH_GAP + 22)
        c.setFillGray(gray)
        c.rect(sx, key_top - SWATCH, SWATCH, SWATCH, stroke=1, fill=1)
        c.setFillGray(1.0 if gray < 0.5 else 0.0)
        c.setFont(FONT, 9.5)
        c.drawCentredString(sx + SWATCH / 2, key_top - SWATCH / 2 - 3.4, text)
        c.setFillGray(0.2)
        c.setFont(FONT, 7.5)
        c.drawString(sx + SWATCH + 4, key_top - SWATCH / 2 - 3.2, f"{pct}%")

    c.showPage()
    c.save()


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
