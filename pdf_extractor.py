"""
pdf_extractor.py
----------------
Extracts structured text blocks from a PDF using pdfplumber.
Preserves layout metadata (font size, bold hints, position) so the
renderer can reconstruct headings, paragraphs, tables, and lists.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional
import pdfplumber

logger = logging.getLogger(__name__)


@dataclass
class TextBlock:
    """
    A single logical block of content from one PDF page.

    block_type: 'heading' | 'paragraph' | 'table' | 'list_item' | 'raw'
    text:       Raw extracted text (UTF-8).
    rows:       For tables — list of rows, each row is list of cell strings.
    font_size:  Approximate font size (points); used to infer headings.
    is_bold:    True if the block appears bold.
    page_num:   1-based page number.
    y_top:      Vertical position from top of page (points); used for ordering.
    """

    block_type: str
    text: str
    rows: List[List[Optional[str]]] = field(default_factory=list)
    font_size: float = 11.0
    is_bold: bool = False
    page_num: int = 1
    y_top: float = 0.0


@dataclass
class PageData:
    """All blocks extracted from one page, in reading order."""

    page_num: int
    width: float
    height: float
    blocks: List[TextBlock] = field(default_factory=list)


def _classify_block(text: str, font_size: float, is_bold: bool) -> str:
    """
    Heuristically classify a text block type from its properties.
    """
    stripped = text.strip()
    if not stripped:
        return "raw"

    # Heading: large font or short bold line
    if font_size >= 14 or (is_bold and len(stripped) < 120 and "\n" not in stripped):
        return "heading"

    # List item: starts with bullet or numbered pattern
    if stripped.startswith(("•", "-", "–", "·", "*")) or (
        len(stripped) > 2
        and stripped[0].isdigit()
        and stripped[1] in (".", ")")
        and stripped[2] == " "
    ):
        return "list_item"

    return "paragraph"


def _chars_to_blocks(chars: list, page_num: int) -> List[TextBlock]:
    """
    Convert pdfplumber character objects into logical text blocks by
    grouping characters that share similar y-position (same line) and
    then grouping nearby lines into paragraphs.
    """
    if not chars:
        return []

    # Sort by vertical then horizontal position
    chars = sorted(chars, key=lambda c: (round(c["top"], 1), c["x0"]))

    # Group into lines by top-position proximity
    lines: List[dict] = []
    current_line: List[dict] = []
    current_top: float = chars[0]["top"]

    for char in chars:
        if abs(char["top"] - current_top) > 3:  # new line threshold
            if current_line:
                lines.append({"chars": current_line, "top": current_top})
            current_line = [char]
            current_top = char["top"]
        else:
            current_line.append(char)

    if current_line:
        lines.append({"chars": current_line, "top": current_top})

    # Build text blocks from lines
    blocks: List[TextBlock] = []
    para_lines: List[dict] = []

    def flush_paragraph():
        if not para_lines:
            return
        text_parts = []
        sizes = []
        bold_flags = []
        for ln in para_lines:
            ln_text = "".join(c.get("text", "") for c in ln["chars"])
            text_parts.append(ln_text)
            for c in ln["chars"]:
                sizes.append(c.get("size", 11.0))
                fname = (c.get("fontname") or "").lower()
                bold_flags.append("bold" in fname or "bd" in fname)

        full_text = "\n".join(text_parts)
        avg_size = sum(sizes) / len(sizes) if sizes else 11.0
        is_bold = bold_flags.count(True) > len(bold_flags) * 0.5
        btype = _classify_block(full_text, avg_size, is_bold)

        blocks.append(
            TextBlock(
                block_type=btype,
                text=full_text,
                font_size=round(avg_size, 1),
                is_bold=is_bold,
                page_num=page_num,
                y_top=para_lines[0]["top"],
            )
        )
        para_lines.clear()

    prev_top = None
    for line in lines:
        if prev_top is not None and (line["top"] - prev_top) > 20:
            # Large gap → new paragraph
            flush_paragraph()
        para_lines.append(line)
        prev_top = line["top"]

    flush_paragraph()
    return blocks


def extract_pdf(pdf_path: str) -> List[PageData]:
    """
    Extract structured content from a PDF file.

    Args:
        pdf_path: Absolute path to the input PDF.

    Returns:
        List of PageData objects, one per page, in page order.

    Raises:
        ValueError:  If the file cannot be opened or is malformed.
        RuntimeError: For unexpected extraction failures.
    """
    pages_data: List[PageData] = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            logger.info("Extracting %d pages from: %s", total, pdf_path)

            for i, page in enumerate(pdf.pages, start=1):
                logger.debug("Extracting page %d/%d", i, total)
                try:
                    page_data = PageData(
                        page_num=i,
                        width=float(page.width),
                        height=float(page.height),
                    )

                    # --- Extract tables first (before text, to exclude their area) ---
                    tables = page.extract_tables()
                    table_bboxes = []

                    for table in (tables or []):
                        if not table:
                            continue
                        # Sanitize cells to strings
                        clean_rows = [
                            [
                                (cell.strip() if isinstance(cell, str) else "")
                                for cell in row
                            ]
                            for row in table
                            if row
                        ]
                        if clean_rows:
                            # Approximate y_top from page settings (best effort)
                            block = TextBlock(
                                block_type="table",
                                text="",
                                rows=clean_rows,
                                page_num=i,
                                y_top=0.0,
                            )
                            page_data.blocks.append(block)

                    # --- Extract remaining text chars ---
                    chars = page.chars or []
                    text_blocks = _chars_to_blocks(chars, page_num=i)
                    page_data.blocks.extend(text_blocks)

                    # Sort all blocks by y_top for reading order
                    page_data.blocks.sort(key=lambda b: b.y_top)
                    pages_data.append(page_data)

                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Error extracting page %d: %s — using raw text fallback", i, exc
                    )
                    # Fallback: plain text extraction
                    try:
                        raw_text = page.extract_text() or ""
                        fallback_block = TextBlock(
                            block_type="raw",
                            text=raw_text,
                            page_num=i,
                        )
                        fallback_page = PageData(
                            page_num=i,
                            width=float(page.width),
                            height=float(page.height),
                            blocks=[fallback_block],
                        )
                        pages_data.append(fallback_page)
                    except Exception as inner_exc:  # noqa: BLE001
                        logger.error(
                            "Fallback extraction failed for page %d: %s", i, inner_exc
                        )

    except pdfplumber.PDFSyntaxError as exc:
        raise ValueError(f"Malformed PDF — cannot parse: {exc}") from exc
    except FileNotFoundError as exc:
        raise ValueError(f"PDF file not found: {pdf_path}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Unexpected error during PDF extraction: {exc}") from exc

    logger.info("Extraction complete: %d pages extracted", len(pages_data))
    return pages_data
