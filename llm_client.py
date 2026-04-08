"""
llm_client.py
-------------
Wraps the enterprise LLM API endpoint.
All translation calls go through chat_completion_mk().
"""

import os
import time
import logging
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

endpoint: str = os.getenv("endpoint", "")
api_key: str = os.getenv("api_key_ramesh", "")

if not api_key:
    raise RuntimeError("api_key_ramesh environment variable not found!")

if not endpoint:
    raise RuntimeError("endpoint environment variable not found!")

HEADERS = {"Content-Type": "application/json"}
MODEL = "gpt-4o"
MAX_CHARS = 3500

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # seconds, doubles each retry


def chat_completion_mk(
    user_prompt: str,
    system_prompt: str,
    model: str = MODEL,
    timeout: int = 60,
    temperature: float = 0,
    max_tokens: int = 4096,
) -> str:
    """
    Call the enterprise LLM API with retry logic.

    Args:
        user_prompt:    The content/text to translate.
        system_prompt:  Instructions for the model (language, rules, etc.).
        model:          Model identifier string.
        timeout:        HTTP request timeout in seconds.
        temperature:    Sampling temperature (0 = deterministic).
        max_tokens:     Max tokens in the response.

    Returns:
        The model's response string, or empty string on failure.

    Raises:
        RuntimeError: After all retries are exhausted.
    """
    payload = {
        "key": api_key,
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

    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug("LLM API call attempt %d/%d", attempt, MAX_RETRIES)
            response = requests.post(
                endpoint,
                headers=HEADERS,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            result = data.get("result", "")
            logger.debug("LLM API call succeeded on attempt %d", attempt)
            return result

        except requests.exceptions.Timeout as exc:
            last_error = exc
            logger.warning("LLM API timeout on attempt %d: %s", attempt, exc)

        except requests.exceptions.HTTPError as exc:
            last_error = exc
            status = exc.response.status_code if exc.response is not None else "N/A"
            logger.warning(
                "LLM API HTTP error %s on attempt %d: %s", status, attempt, exc
            )
            # Do not retry on client-side errors (4xx) except 429
            if (
                exc.response is not None
                and 400 <= exc.response.status_code < 500
                and exc.response.status_code != 429
            ):
                break

        except requests.exceptions.RequestException as exc:
            last_error = exc
            logger.warning(
                "LLM API request error on attempt %d: %s", attempt, exc
            )

        if attempt < MAX_RETRIES:
            sleep_time = RETRY_BACKOFF * (2 ** (attempt - 1))
            logger.info("Retrying in %.1f seconds...", sleep_time)
            time.sleep(sleep_time)

    raise RuntimeError(
        f"LLM API failed after {MAX_RETRIES} attempts. Last error: {last_error}"
    )
