import logging
from typing import List, Optional

import fitz

from pdf_translator import config
from pdf_translator.models import TextSpan
from pdf_translator.translator import translate_lines

logger = logging.getLogger(__name__)


class PDFTranslationError(Exception):
    pass


def extract_text_spans(pdf_bytes: bytes) -> tuple[List[TextSpan], fitz.Document]:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise PDFTranslationError(f"Malformed or unreadable PDF: {exc}") from exc

    spans: list[TextSpan] = []
    for page_number, page in enumerate(doc):
        page_dict = page.get_text("dict")
        for block_no, block in enumerate(page_dict.get("blocks", [])):
            if block.get("type") != 0:
                continue
            for line_no, line in enumerate(block.get("lines", [])):
                for span_no, span in enumerate(line.get("spans", [])):
                    text = span.get("text", "")
                    if not text.strip():
                        continue
                    spans.append(
                        TextSpan(
                            page_number=page_number,
                            block_no=block_no,
                            line_no=line_no,
                            span_no=span_no,
                            text=text,
                            bbox=tuple(span.get("bbox", (0, 0, 0, 0))),
                            font=span.get("font", "helv"),
                            size=float(span.get("size", 10)),
                            color=int(span.get("color", 0)),
                            flags=int(span.get("flags", 0)),
                        )
                    )
    return spans, doc


def _chunk_spans(spans: List[TextSpan], max_chars: int = config.MAX_CHARS) -> list[list[int]]:
    chunks: list[list[int]] = []
    current: list[int] = []
    count = 0

    for i, span in enumerate(spans):
        txt_len = len(span.text)
        if current and (count + txt_len > max_chars):
            chunks.append(current)
            current = []
            count = 0
        current.append(i)
        count += txt_len

    if current:
        chunks.append(current)
    return chunks


def _validate_length_ratio(source: str, translated: str, low: float = 0.25, high: float = 4.0) -> bool:
    s = len(source.strip())
    t = len(translated.strip())
    if s == 0:
        return True
    ratio = t / s
    return low <= ratio <= high


def _validate_max_pages(max_pages: Optional[int], total_pages: int) -> Optional[int]:
    if max_pages is None:
        return None
    if max_pages <= 0:
        raise PDFTranslationError("max_pages must be greater than 0")
    return min(max_pages, total_pages)


def _render_span_text(page: fitz.Page, rect: fitz.Rect, text: str, span: TextSpan) -> bool:
    """Render text into the original span rectangle.

    Returns True when any text is successfully drawn.
    """
    candidate_fonts = [span.font, "helv"]
    candidate_sizes = [span.size, max(6.0, span.size - 1), max(5.0, span.size - 2)]

    for font in candidate_fonts:
        for size in candidate_sizes:
            try:
                rc = page.insert_textbox(
                    rect,
                    text,
                    fontname=font,
                    fontsize=size,
                    color=_int_to_rgb(span.color),
                    align=fitz.TEXT_ALIGN_LEFT,
                    overlay=True,
                )
                if rc >= 0:
                    return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("Textbox render failed (font=%s size=%s): %s", font, size, exc)

    # Final hard fallback: place text baseline near top-left so box is never left blank.
    try:
        page.insert_text(
            fitz.Point(rect.x0, rect.y0 + max(5.0, span.size)),
            text,
            fontname="helv",
            fontsize=max(5.0, span.size),
            color=_int_to_rgb(span.color),
            overlay=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Final fallback render failed: %s", exc)
        return False


def translate_pdf(pdf_bytes: bytes, target_language: str, max_pages: Optional[int] = None) -> bytes:
    spans, source_doc = extract_text_spans(pdf_bytes)
    page_limit = _validate_max_pages(max_pages, source_doc.page_count)

    if page_limit is None:
        spans_to_translate = spans
    else:
        spans_to_translate = [s for s in spans if s.page_number < page_limit]

    if not spans_to_translate:
        raise PDFTranslationError("No extractable text found in selected page range")

    translated_by_index = {i: span.text for i, span in enumerate(spans)}
    index_map = [i for i, s in enumerate(spans) if (page_limit is None or s.page_number < page_limit)]

    selected_spans = [spans[i] for i in index_map]
    for chunk_indexes in _chunk_spans(selected_spans):
        source_lines = [selected_spans[i].text for i in chunk_indexes]
        out_lines = translate_lines(source_lines, target_language)

        for offset, local_idx in enumerate(chunk_indexes):
            global_idx = index_map[local_idx]
            src = selected_spans[local_idx].text
            candidate = out_lines[offset]
            if _validate_length_ratio(src, candidate):
                translated_by_index[global_idx] = candidate
            else:
                logger.warning(
                    "Length validation failed on page %s span %s; using original text.",
                    selected_spans[local_idx].page_number,
                    global_idx,
                )

    output_doc = fitz.open()
    for page_no in range(source_doc.page_count):
        src_page = source_doc[page_no]
        dst_page = output_doc.new_page(width=src_page.rect.width, height=src_page.rect.height)
        dst_page.show_pdf_page(dst_page.rect, source_doc, page_no)

    for i, span in enumerate(spans):
        if page_limit is not None and span.page_number >= page_limit:
            continue

        page = output_doc[span.page_number]
        rect = fitz.Rect(span.bbox)

        # Cover original source text first, then draw translated text above.
        page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)

        rendered = _render_span_text(page, rect, translated_by_index[i], span)
        if not rendered:
            logger.warning("Failed to render translated span; preserving source text visible.")

    out_bytes = output_doc.tobytes(garbage=4, deflate=True)
    output_doc.close()
    source_doc.close()
    return out_bytes


def _int_to_rgb(color: int) -> tuple[float, float, float]:
    r = ((color >> 16) & 255) / 255
    g = ((color >> 8) & 255) / 255
    b = (color & 255) / 255
    return r, g, b
