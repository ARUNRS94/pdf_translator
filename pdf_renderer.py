"""
pdf_renderer.py
---------------
Reconstructs a translated PDF from PageData objects using ReportLab.
Preserves:
  - Heading hierarchy (font size / bold)
  - Paragraphs with correct line breaks
  - Bullet / numbered lists
  - Tables with borders
  - Page dimensions from the source PDF
  - UTF-8 text (accented characters, multilingual glyphs)
"""

import logging
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import pt
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY

from pdf_extractor import PageData, TextBlock

logger = logging.getLogger(__name__)

# ── Font registration ──────────────────────────────────────────────────────────
# Try to register DejaVu for full Unicode support.
# Falls back to Helvetica (ASCII-safe) if font files are absent.
_FONT_NORMAL = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"

try:
    import os
    _DEJAVU_PATHS = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/DejaVuSans.ttf",
    ]
    _DEJAVU_BOLD_PATHS = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/DejaVuSans-Bold.ttf",
    ]
    for p in _DEJAVU_PATHS:
        if os.path.exists(p):
            pdfmetrics.registerFont(TTFont("DejaVuSans", p))
            _FONT_NORMAL = "DejaVuSans"
            break
    for p in _DEJAVU_BOLD_PATHS:
        if os.path.exists(p):
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", p))
            _FONT_BOLD = "DejaVuSans-Bold"
            break
except Exception as exc:  # noqa: BLE001
    logger.warning("Could not register DejaVu fonts: %s — using Helvetica", exc)


# ── Style factory ──────────────────────────────────────────────────────────────
def _build_styles(font_normal: str, font_bold: str) -> dict:
    base = getSampleStyleSheet()

    styles = {
        "heading1": ParagraphStyle(
            "Heading1Custom",
            fontName=font_bold,
            fontSize=16,
            leading=20,
            spaceAfter=8,
            spaceBefore=12,
            alignment=TA_LEFT,
        ),
        "heading2": ParagraphStyle(
            "Heading2Custom",
            fontName=font_bold,
            fontSize=13,
            leading=17,
            spaceAfter=6,
            spaceBefore=10,
            alignment=TA_LEFT,
        ),
        "heading3": ParagraphStyle(
            "Heading3Custom",
            fontName=font_bold,
            fontSize=11,
            leading=15,
            spaceAfter=4,
            spaceBefore=8,
            alignment=TA_LEFT,
        ),
        "paragraph": ParagraphStyle(
            "ParagraphCustom",
            fontName=font_normal,
            fontSize=10,
            leading=14,
            spaceAfter=6,
            spaceBefore=2,
            alignment=TA_JUSTIFY,
        ),
        "list_item": ParagraphStyle(
            "ListItemCustom",
            fontName=font_normal,
            fontSize=10,
            leading=14,
            leftIndent=18,
            spaceAfter=3,
            spaceBefore=1,
            bulletIndent=6,
        ),
        "raw": ParagraphStyle(
            "RawCustom",
            fontName=font_normal,
            fontSize=10,
            leading=14,
            spaceAfter=4,
        ),
        "table_cell": ParagraphStyle(
            "TableCellCustom",
            fontName=font_normal,
            fontSize=9,
            leading=12,
        ),
        "table_header": ParagraphStyle(
            "TableHeaderCustom",
            fontName=font_bold,
            fontSize=9,
            leading=12,
        ),
    }
    return styles


def _heading_style(block: TextBlock, styles: dict) -> ParagraphStyle:
    """Pick heading level based on font size."""
    if block.font_size >= 16:
        return styles["heading1"]
    if block.font_size >= 13:
        return styles["heading2"]
    return styles["heading3"]


def _safe_paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    """
    Create a Paragraph, escaping XML-special characters to avoid ReportLab parse errors.
    """
    safe = (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    try:
        return Paragraph(safe, style)
    except Exception:  # noqa: BLE001
        # Last resort: strip to ASCII
        ascii_safe = safe.encode("ascii", errors="replace").decode("ascii")
        return Paragraph(ascii_safe, style)


def _build_table_flowable(block: TextBlock, styles: dict, page_width: float) -> Table:
    """Convert a TextBlock of type 'table' into a ReportLab Table."""
    rows = block.rows
    if not rows:
        return Table([[""]])

    num_cols = max(len(row) for row in rows)
    col_width = (page_width - 72) / max(num_cols, 1)  # 36pt margin each side

    data = []
    for r_idx, row in enumerate(rows):
        cell_style = styles["table_header"] if r_idx == 0 else styles["table_cell"]
        cell_row = []
        for col_idx in range(num_cols):
            cell_text = row[col_idx] if col_idx < len(row) else ""
            cell_row.append(_safe_paragraph(cell_text or "", cell_style))
        data.append(cell_row)

    col_widths = [col_width] * num_cols

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9E1F2")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#AAAAAA")),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
            ]
        )
    )
    return table


def _blocks_to_flowables(
    blocks: List[TextBlock], styles: dict, page_width: float
) -> list:
    """Convert a list of TextBlocks to ReportLab flowables."""
    flowables = []

    for block in blocks:
        if block.block_type == "table":
            try:
                tbl = _build_table_flowable(block, styles, page_width)
                flowables.append(Spacer(1, 6))
                flowables.append(tbl)
                flowables.append(Spacer(1, 8))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not render table: %s", exc)

        elif block.block_type == "heading":
            style = _heading_style(block, styles)
            flowables.append(_safe_paragraph(block.text, style))
            flowables.append(Spacer(1, 4))

        elif block.block_type == "list_item":
            # Prefix bullet if not already present
            text = block.text.strip()
            if not text.startswith(("•", "-", "–")):
                text = "• " + text
            flowables.append(_safe_paragraph(text, styles["list_item"]))

        elif block.block_type in ("paragraph", "raw"):
            # Split on newlines to preserve line breaks within a block
            for line in block.text.split("\n"):
                if line.strip():
                    flowables.append(_safe_paragraph(line, styles["paragraph"]))
                else:
                    flowables.append(Spacer(1, 4))

    return flowables


def render_pdf(
    pages: List[PageData],
    output_path: str,
) -> str:
    """
    Render translated PageData into a PDF file at output_path.

    Page dimensions are taken from the source PDF (per page).
    The first page's dimensions are used for the document template;
    subsequent pages use the same layout (ReportLab limitation).

    Args:
        pages:       List of translated PageData objects.
        output_path: Destination file path for the output PDF.

    Returns:
        output_path on success.

    Raises:
        RuntimeError: If rendering fails.
    """
    if not pages:
        raise ValueError("No pages to render.")

    styles = _build_styles(_FONT_NORMAL, _FONT_BOLD)

    # Use first page dimensions as document size
    first_page = pages[0]
    doc_width = first_page.width * pt if first_page.width > 0 else A4[0]
    doc_height = first_page.height * pt if first_page.height > 0 else A4[1]
    # pdfplumber returns dimensions already in points; avoid double-scaling
    doc_width = first_page.width if first_page.width > 200 else A4[0]
    doc_height = first_page.height if first_page.height > 200 else A4[1]

    MARGIN = 36  # 0.5 inch

    try:
        doc = BaseDocTemplate(
            output_path,
            pagesize=(doc_width, doc_height),
            leftMargin=MARGIN,
            rightMargin=MARGIN,
            topMargin=MARGIN,
            bottomMargin=MARGIN,
        )

        frame = Frame(
            MARGIN, MARGIN,
            doc_width - 2 * MARGIN,
            doc_height - 2 * MARGIN,
            id="main",
        )
        doc.addPageTemplates([PageTemplate(id="main_template", frames=[frame])])

        story = []
        page_width = doc_width - 2 * MARGIN

        for p_idx, page in enumerate(pages):
            flowables = _blocks_to_flowables(page.blocks, styles, page_width)
            story.extend(flowables)

            # Add page break between pages (not after the last)
            if p_idx < len(pages) - 1:
                story.append(PageBreak())

        doc.build(story)
        logger.info("PDF rendered successfully: %s", output_path)
        return output_path

    except Exception as exc:  # noqa: BLE001
        logger.exception("PDF rendering failed: %s", exc)
        raise RuntimeError(f"PDF rendering failed: {exc}") from exc
