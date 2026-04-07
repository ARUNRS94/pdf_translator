import logging
import re
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


def _looks_like_section_heading(text: str) -> bool:
    t = text.strip()
    return bool(re.match(r"^\d+(\.\d+)*\s+", t))


def _looks_like_footer_emphasis(text: str) -> bool:
    t = text.strip().lower()
    return bool(re.search(r"\brev\.?\b|\brév\.?\b", t))


def _font_candidates(span: TextSpan, text: str) -> list[str]:
    name = (span.font or "").lower()
    strong_name = any(k in name for k in ["bold", "black", "semibold", "demi"])
    emphasis = _looks_like_section_heading(text) or _looks_like_footer_emphasis(text)

    bold = strong_name or bool(span.flags & 16) or emphasis
    italic = ("italic" in name or "oblique" in name) or bool(span.flags & 2)

    styled = "helv"
    if bold and italic:
        styled = "helvBI"
    elif bold:
        styled = "helvB"
    elif italic:
        styled = "helvI"

    preferred = [styled, span.font, "helvB", "helv"] if bold else [span.font, styled, "helv"]

    out = []
    for f in preferred:
        if f and f not in out:
            out.append(f)
    return out




def _draw_text(page: fitz.Page, point: fitz.Point, text: str, font: str, size: float, color: tuple[float, float, float], faux_bold: bool = False) -> None:
    page.insert_text(point, text, fontname=font, fontsize=size, color=color, overlay=True)
    if faux_bold:
        page.insert_text(fitz.Point(point.x + 0.2, point.y), text, fontname=font, fontsize=size, color=color, overlay=True)

def _fit_fontsize_for_width(text: str, font: str, preferred_size: float, max_width: float) -> float:
    try:
        width = fitz.get_text_length(text, fontname=font, fontsize=preferred_size)
        if width <= 0:
            return preferred_size
        if width <= max_width:
            return preferred_size
        ratio = max_width / width
        fitted = preferred_size * ratio
        return max(5.0, min(preferred_size, fitted))
    except Exception:
        return preferred_size




def _truncate_to_width(text: str, font: str, size: float, max_width: float) -> str:
    if fitz.get_text_length(text, fontname=font, fontsize=size) <= max_width:
        return text

    out = text
    while out and fitz.get_text_length(out, fontname=font, fontsize=size) > max_width:
        out = out[:-1]
    return out.rstrip()


def _render_toc_line(page: fitz.Page, rect: fitz.Rect, text: str, font: str, size: float, color: tuple[float, float, float], faux_bold: bool = False) -> bool:
    m = re.match(r"^(.*?)(\.{3,})(\s*\d+)\s*$", text)
    if not m:
        return False

    title = m.group(1).rstrip()
    page_num = m.group(3).strip()

    try:
        num_w = fitz.get_text_length(page_num, fontname=font, fontsize=size)
        x_num = max(rect.x0 + rect.width * 0.65, rect.x1 - num_w)
        baseline = rect.y0 + max(size, rect.height * 0.8)

        _draw_text(page, fitz.Point(x_num, baseline), page_num, font, size, color, faux_bold=faux_bold)

        title_space = max(5.0, x_num - rect.x0 - 6)
        title_size = _fit_fontsize_for_width(title, font, size, title_space)
        title_text = _truncate_to_width(title, font, title_size, title_space)
        title_w = fitz.get_text_length(title_text, fontname=font, fontsize=title_size)
        dots_start = rect.x0 + title_w + 2
        dots_end = x_num - 2
        if dots_end > dots_start:
            dot_w = max(1.0, fitz.get_text_length(".", fontname=font, fontsize=size))
            dot_count = int((dots_end - dots_start) / dot_w)
            if dot_count > 2:
                _draw_text(page, fitz.Point(dots_start, baseline), "." * dot_count, font, size, color, faux_bold=faux_bold)

        _draw_text(page, fitz.Point(rect.x0, baseline), title_text, font, title_size, color, faux_bold=faux_bold)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("TOC render fallback failed: %s", exc)
        return False


def _render_span_text(page: fitz.Page, rect: fitz.Rect, text: str, span: TextSpan) -> bool:
    candidate_fonts = _font_candidates(span, text)
    base_size = span.size
    color = _int_to_rgb(span.color)

    emphasis = _looks_like_section_heading(text) or _looks_like_footer_emphasis(text)

    for font in candidate_fonts:
        size_fit = _fit_fontsize_for_width(text, font, base_size, max(5.0, rect.width - 1))
        candidate_sizes = [size_fit, max(5.0, size_fit - 0.5), max(5.0, size_fit - 1.0)]

        for size in candidate_sizes:
            if _render_toc_line(page, rect, text, font, size, color, faux_bold=emphasis):
                return True

            # For large headings, force single-line baseline render first to avoid overlap with underline.
            if span.size >= 16:
                try:
                    baseline = fitz.Point(rect.x0, rect.y0 + max(size * 0.95, rect.height * 0.7))
                    _draw_text(page, baseline, text, font, size, color, faux_bold=emphasis)
                    return True
                except Exception:
                    pass

            try:
                rc = page.insert_textbox(
                    rect,
                    text,
                    fontname=font,
                    fontsize=size,
                    color=color,
                    align=fitz.TEXT_ALIGN_LEFT,
                    overlay=True,
                )
                if rc >= 0:
                    return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("Textbox render failed (font=%s size=%s): %s", font, size, exc)

            # Avoid overflow artifacts in normal body text: if textbox fails, caller will fallback to source text.
            if span.size >= 16 or emphasis:
                try:
                    baseline = fitz.Point(rect.x0, rect.y0 + max(size * 0.95, rect.height * 0.78))
                    _draw_text(page, baseline, text, font, size, color, faux_bold=emphasis)
                    return True
                except Exception:
                    pass

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
    output_pages = page_limit if page_limit is not None else source_doc.page_count
    for page_no in range(output_pages):
        src_page = source_doc[page_no]
        dst_page = output_doc.new_page(width=src_page.rect.width, height=src_page.rect.height)
        dst_page.show_pdf_page(dst_page.rect, source_doc, page_no)

    for i, span in enumerate(spans):
        if span.page_number >= output_pages:
            continue

        page = output_doc[span.page_number]
        rect = fitz.Rect(span.bbox)
        page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)

        if not _render_span_text(page, rect, translated_by_index[i], span):
            logger.warning("Failed to render translated span; keeping source text for span %s.", i)
            _render_span_text(page, rect, span.text, span)

    out_bytes = output_doc.tobytes(garbage=4, deflate=True)
    output_doc.close()
    source_doc.close()
    return out_bytes


def _int_to_rgb(color: int) -> tuple[float, float, float]:
    r = ((color >> 16) & 255) / 255
    g = ((color >> 8) & 255) / 255
    b = (color & 255) / 255
    return r, g, b
