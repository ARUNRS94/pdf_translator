"""
translation_service.py
-----------------------
Handles all translation logic:
  - Chunking text to stay within MAX_CHARS
  - Building system/user prompts
  - Calling the LLM
  - Validating translated output
  - Translating TextBlock and table cell collections
"""

import logging
import re
from typing import List, Optional, Tuple

from llm_client import chat_completion_mk, MAX_CHARS
from pdf_extractor import TextBlock, PageData

logger = logging.getLogger(__name__)

# Patterns that must NOT be translated
_SKIP_PATTERN = re.compile(
    r"^("
    r"https?://\S+"           # URLs
    r"|ftp://\S+"
    r"|\S+@\S+\.\S+"          # Email addresses
    r"|[\d\s\-\+\(\)/\.,%]+$"  # Pure numbers / dates / symbols
    r"|[A-Z0-9_\-]{2,}$"      # Codes / acronyms
    r")$",
    re.IGNORECASE,
)

_CODE_BLOCK_PATTERN = re.compile(r"```[\s\S]*?```|`[^`]+`")


def _should_skip(text: str) -> bool:
    """Return True if the text should NOT be sent to the LLM."""
    stripped = text.strip()
    if not stripped:
        return True
    if _SKIP_PATTERN.match(stripped):
        return True
    return False


def _build_system_prompt(target_language: str) -> str:
    return (
        f"You are a professional document translator. "
        f"Translate the following text into {target_language}.\n\n"
        "STRICT RULES:\n"
        "1. Translate ONLY human-readable prose and labels.\n"
        "2. Do NOT change: numbers, dates, symbols, URLs, file paths, "
        "email addresses, code snippets, or technical identifiers.\n"
        "3. Do NOT add, remove, reorder, summarize, or paraphrase content.\n"
        "4. Preserve all line breaks, bullet markers, numbering, and "
        "indentation exactly as in the input.\n"
        "5. Return ONLY the translated text — no explanations, no markdown "
        "wrappers, no preamble.\n"
        "6. If a word or phrase should remain untranslated (brand names, "
        "acronyms, product names), keep it as-is.\n"
    )


def _chunk_text(text: str, max_chars: int = MAX_CHARS) -> List[str]:
    """
    Split text into chunks ≤ max_chars, breaking at paragraph boundaries
    when possible, then at sentence boundaries, then hard-truncating.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    remaining = text

    while len(remaining) > max_chars:
        # Try paragraph break
        split_at = remaining.rfind("\n\n", 0, max_chars)
        if split_at == -1:
            # Try newline
            split_at = remaining.rfind("\n", 0, max_chars)
        if split_at == -1:
            # Try sentence end
            split_at = remaining.rfind(". ", 0, max_chars)
            if split_at != -1:
                split_at += 1  # include the period
        if split_at == -1 or split_at == 0:
            split_at = max_chars

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")

    if remaining:
        chunks.append(remaining)

    return chunks


def _validate_translation(original: str, translated: str) -> bool:
    """
    Lightweight validation: translated text should be non-empty and
    within a reasonable length ratio of the original.
    """
    if not translated or not translated.strip():
        return False
    orig_len = len(original.strip())
    trans_len = len(translated.strip())
    # Allow translated text to be 30%–300% the length of the original
    if orig_len == 0:
        return True
    ratio = trans_len / orig_len
    if ratio < 0.3 or ratio > 3.0:
        logger.warning(
            "Translation length ratio out of bounds: %.2f (orig=%d, trans=%d)",
            ratio, orig_len, trans_len,
        )
        return False
    return True


def translate_text(text: str, target_language: str) -> str:
    """
    Translate a single text string into target_language.
    Handles chunking automatically.
    Falls back to the original text if translation fails.

    Args:
        text:            Source text (UTF-8).
        target_language: E.g. 'French', 'German', 'Spanish'.

    Returns:
        Translated text, or original text on failure.
    """
    if _should_skip(text):
        return text

    system_prompt = _build_system_prompt(target_language)
    chunks = _chunk_text(text)
    translated_chunks: List[str] = []

    for idx, chunk in enumerate(chunks, start=1):
        if _should_skip(chunk):
            translated_chunks.append(chunk)
            continue

        try:
            logger.debug(
                "Translating chunk %d/%d (%d chars)", idx, len(chunks), len(chunk)
            )
            translated = chat_completion_mk(
                user_prompt=chunk,
                system_prompt=system_prompt,
                temperature=0,
                max_tokens=4096,
            )

            # Ensure UTF-8 safety
            translated = translated.encode("utf-8", errors="replace").decode("utf-8")

            if not _validate_translation(chunk, translated):
                logger.warning(
                    "Validation failed for chunk %d — falling back to original", idx
                )
                translated_chunks.append(chunk)
            else:
                translated_chunks.append(translated)

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Translation failed for chunk %d: %s — using original text", idx, exc
            )
            translated_chunks.append(chunk)  # Graceful fallback

    # Re-join chunks with the same delimiter used to split them
    return "\n\n".join(translated_chunks) if len(chunks) > 1 else (
        translated_chunks[0] if translated_chunks else text
    )


def translate_table_rows(
    rows: List[List[Optional[str]]], target_language: str
) -> List[List[str]]:
    """
    Translate each cell of a table individually.
    Preserves table structure (rows × cols) exactly.
    """
    translated_rows: List[List[str]] = []
    for row in rows:
        translated_row: List[str] = []
        for cell in row:
            if cell is None or cell.strip() == "":
                translated_row.append(cell or "")
            else:
                translated_row.append(translate_text(cell, target_language))
        translated_rows.append(translated_row)
    return translated_rows


def translate_page(page: PageData, target_language: str) -> PageData:
    """
    Translate all blocks on a single page in-place (mutates block.text / block.rows).
    Returns the modified PageData.
    """
    for block in page.blocks:
        try:
            if block.block_type == "table":
                block.rows = translate_table_rows(block.rows, target_language)
            elif block.block_type in ("heading", "paragraph", "list_item", "raw"):
                if block.text.strip():
                    block.text = translate_text(block.text, target_language)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to translate block (type=%s, page=%d): %s — keeping original",
                block.block_type,
                page.page_num,
                exc,
            )
    return page


def translate_all_pages(
    pages: List[PageData],
    target_language: str,
    progress_callback=None,
) -> List[PageData]:
    """
    Translate all pages sequentially.

    Args:
        pages:             Extracted PageData list from pdf_extractor.
        target_language:   Target language string.
        progress_callback: Optional callable(current, total) for progress tracking.

    Returns:
        List of translated PageData objects.
    """
    total = len(pages)
    logger.info("Starting translation of %d pages into %s", total, target_language)

    for idx, page in enumerate(pages, start=1):
        logger.info("Translating page %d/%d", idx, total)
        translate_page(page, target_language)
        if progress_callback:
            try:
                progress_callback(idx, total)
            except Exception:  # noqa: BLE001
                pass

    logger.info("Translation complete.")
    return pages
