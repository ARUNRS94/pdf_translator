import json
import logging
import time
from typing import List

import requests

from pdf_translator import config

logger = logging.getLogger(__name__)


def chat_completion_mk(
    user_prompt,
    system_prompt,
    model=config.MODEL,
    timeout=config.DEFAULT_TIMEOUT,
    temperature=0,
    max_tokens=4096,
):
    if not config.api_key:
        raise RuntimeError("api_key_ramesh variable not found!")
    if not config.endpoint:
        raise RuntimeError("endpoint variable not found!")

    payload = {
        "key": config.api_key,
        "model": model,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
        "app_name": "pdf_translator",
        "user_name": "arun.rameshkumar.suseela",
        "department_name": "58000 Technology",
        "vendor": "openai",
    }

    response = requests.post(
        config.endpoint,
        headers=config.HEADERS,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()

    data = response.json()
    return data.get("result", "")


def _system_prompt(target_language: str) -> str:
    return (
        "You are a strict translation engine for enterprise PDFs. "
        f"Translate content into {target_language}. "
        "Rules: preserve order, keep one output line per input line, "
        "do not alter numbers/dates/symbols/references/URLs/emails/file paths/code, "
        "no summarization, no omissions, no additions."
    )


def translate_lines(lines: List[str], target_language: str) -> List[str]:
    if not lines:
        return []

    user_prompt = (
        "Translate each line and return ONLY valid JSON with schema "
        '{"translations":[{"i":0,"text":"..."}]}. '\
        "Indices must match input exactly.\n"
    )
    for i, line in enumerate(lines):
        user_prompt += f"{i}: {line}\n"

    last_error = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            raw = chat_completion_mk(
                user_prompt=user_prompt,
                system_prompt=_system_prompt(target_language),
                temperature=0,
            )
            parsed = _parse_translation_json(raw, len(lines))
            return parsed
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("Translation attempt %s/%s failed: %s", attempt, config.MAX_RETRIES, exc)
            if attempt < config.MAX_RETRIES:
                time.sleep(config.BACKOFF_SECONDS * attempt)

    logger.error("All translation retries exhausted, using original text. Error: %s", last_error)
    return lines


def _parse_translation_json(raw: str, expected_size: int) -> List[str]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()

    payload = json.loads(cleaned)
    items = payload.get("translations", [])

    result = [""] * expected_size
    for item in items:
        i = item.get("i")
        text = item.get("text", "")
        if isinstance(i, int) and 0 <= i < expected_size:
            result[i] = text

    if any(v == "" for v in result):
        raise ValueError("Missing translated lines in model response")

    return result
