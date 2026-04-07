import io
import logging
from typing import List

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


def translate_pdf(pdf_bytes: bytes, target_language: str) -> bytes:
    spans, source_doc = extract_text_spans(pdf_bytes)
    if not spans:
        raise PDFTranslationError("No extractable text found in PDF")

    translated_text = [s.text for s in spans]
    for chunk_indexes in _chunk_spans(spans):
        source_lines = [spans[i].text for i in chunk_indexes]
        out_lines = translate_lines(source_lines, target_language)
        for offset, idx in enumerate(chunk_indexes):
            src = spans[idx].text
            candidate = out_lines[offset]
            if _validate_length_ratio(src, candidate):
                translated_text[idx] = candidate
            else:
                logger.warning(
                    "Length validation failed on page %s span %s; using original text.",
                    spans[idx].page_number,
                    idx,
                )
                translated_text[idx] = src

    output_doc = fitz.open()
    for page_no in range(source_doc.page_count):
        src_page = source_doc[page_no]
        dst_page = output_doc.new_page(width=src_page.rect.width, height=src_page.rect.height)
        dst_page.show_pdf_page(dst_page.rect, source_doc, page_no)

    for i, span in enumerate(spans):
        page = output_doc[span.page_number]
        rect = fitz.Rect(span.bbox)

        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

        try:
            page.insert_textbox(
                rect,
                translated_text[i],
                fontname=span.font,
                fontsize=span.size,
                color=_int_to_rgb(span.color),
                align=fitz.TEXT_ALIGN_LEFT,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to render translated span; fallback to original: %s", exc)
            page.insert_textbox(
                rect,
                span.text,
                fontsize=span.size,
                color=_int_to_rgb(span.color),
                align=fitz.TEXT_ALIGN_LEFT,
            )

    out_bytes = output_doc.tobytes(garbage=4, deflate=True)
    output_doc.close()
    source_doc.close()
    return out_bytes


def _int_to_rgb(color: int) -> tuple[float, float, float]:
    r = ((color >> 16) & 255) / 255
    g = ((color >> 8) & 255) / 255
    b = (color & 255) / 255
    return r, g, b
