"""
LLM client abstraction.

Phase 1: Gemini-only implementation.
The abstract LLMClient base class means Phase 2 can add Groq / OpenRouter
by subclassing — no existing call sites change.
"""

import json
import time
import logging
from abc import ABC, abstractmethod
from typing import Any

import google.genai as genai
from google.genai import types

from app.config import GEMINI_API_KEY
from app.constants import GEMINI_MODEL, LLM_MAX_RETRIES, LLM_BASE_DELAY_SECONDS

logger = logging.getLogger(__name__)


class LLMClient(ABC):
    """
    Abstract interface for an LLM provider.
    Subclass and implement generate() to add a new provider.
    """

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> dict:
        """
        Send system + user prompts and return parsed JSON.

        Raises
        ------
        RuntimeError  if the call fails after all retries.
        """
        ...


class GeminiClient(LLMClient):
    """
    Google Gemini API client.
    - JSON-mode output via response_mime_type
    - Exponential backoff on 429 / ResourceExhausted
    """

    def __init__(self) -> None:
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model_name = GEMINI_MODEL

    def generate(self, system_prompt: str, user_prompt: str) -> dict:
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            temperature=0.4,
        )

        MAX_JSON_RETRIES = 3   # separate budget for malformed JSON
        MAX_API_RETRIES = LLM_MAX_RETRIES

        last_exc: Exception | None = None
        last_raw: str = ""
        json_failures = 0
        api_failures = 0

        while (json_failures < MAX_JSON_RETRIES) and (api_failures < MAX_API_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=user_prompt,
                    config=config,
                )
                # Gemini may return text with markdown fences — strip them.
                raw = response.text.strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[-1]  # drop opening fence
                    raw = raw.rsplit("```", 1)[0]  # drop closing fence
                last_raw = raw

                # Try parsing, and if it fails, try repair immediately
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    repaired = _repair_json(raw)
                    if repaired is not None:
                        logger.info("JSON repair succeeded on attempt %d", json_failures + 1)
                        return repaired
                    json_failures += 1
                    last_exc = json.JSONDecodeError("malformed", raw[:100], 0)
                    logger.warning(
                        "JSON parse+repair failed (json attempt %d/%d) — retrying LLM call …",
                        json_failures,
                        MAX_JSON_RETRIES,
                    )
                    time.sleep(1)
                    continue

            except Exception as exc:
                last_exc = exc
                err = str(exc).lower()
                rate_limited = any(
                    tok in err
                    for tok in ("429", "resource exhausted", "rate", "quota")
                )
                if rate_limited:
                    api_failures += 1
                    # Cap delay at 60s, and use longer base delay
                    delay = min(60, LLM_BASE_DELAY_SECONDS * (2 ** api_failures))
                    logger.warning(
                        "Rate-limited (api attempt %d/%d), retrying in %ds …",
                        api_failures,
                        MAX_API_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error("LLM call failed (non-retryable): %s", exc)
                    raise

        raise RuntimeError(
            f"LLM call failed after {json_failures} JSON retries + {api_failures} API retries: {last_exc}"
        )


def _repair_json(raw: str) -> dict | None:
    """
    Best-effort repair of common JSON issues from LLM output:
    - Trailing commas before } or ]
    - Unescaped control characters inside strings
    - Smart quotes → straight quotes
    - Truncated JSON (add missing closing braces)
    - Extract JSON object if surrounded by extra text
    """
    import re

    strategies = [
        _try_extract_json,
        _try_fix_common_issues,
        _try_fix_control_chars,
    ]

    for strategy in strategies:
        result = strategy(raw)
        if result is not None:
            return result

    logger.warning("All JSON repair strategies failed")
    return None


def _try_extract_json(raw: str) -> dict | None:
    """Extract the first complete JSON object from the text."""
    # Find the first { and try increasingly large substrings
    start = raw.find("{")
    if start == -1:
        return None
    for end in range(len(raw), start, -1):
        if raw[end - 1] == "}":
            try:
                return json.loads(raw[start:end])
            except json.JSONDecodeError:
                continue
    return None


def _try_fix_common_issues(raw: str) -> dict | None:
    """Fix trailing commas, smart quotes, and missing closing braces."""
    import re

    try:
        # Replace smart quotes
        fixed = raw.replace("\u201c", '"').replace("\u201d", '"')
        fixed = fixed.replace("\u2018", "'").replace("\u2019", "'")

        # Remove trailing commas: ,} or ,]
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)

        # Balance braces — add missing closing braces/brackets
        open_braces = fixed.count("{") - fixed.count("}")
        open_brackets = fixed.count("[") - fixed.count("]")
        if open_braces > 0:
            fixed += "}" * open_braces
        if open_brackets > 0:
            fixed += "]" * open_brackets

        return json.loads(fixed)
    except (json.JSONDecodeError, Exception):
        return None


def _try_fix_control_chars(raw: str) -> dict | None:
    """Remove or escape control characters that break JSON parsing."""
    import re

    try:
        # Replace smart quotes
        fixed = raw.replace("\u201c", '"').replace("\u201d", '"')
        fixed = fixed.replace("\u2018", "'").replace("\u2019", "'")

        # Remove trailing commas
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)

        # Escape unescaped control characters inside strings.
        # Strategy: process char-by-char, tracking if we're inside a string.
        result = []
        in_string = False
        escape_next = False
        for ch in fixed:
            if escape_next:
                result.append(ch)
                escape_next = False
                continue
            if ch == "\\" and in_string:
                result.append(ch)
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                result.append(ch)
                continue
            if in_string and ord(ch) < 32:
                # Escape the control character
                result.append(f"\\u{ord(ch):04x}")
                continue
            result.append(ch)

        fixed = "".join(result)

        # Balance braces
        open_braces = fixed.count("{") - fixed.count("}")
        open_brackets = fixed.count("[") - fixed.count("]")
        if open_braces > 0:
            fixed += "}" * open_braces
        if open_brackets > 0:
            fixed += "]" * open_brackets

        return json.loads(fixed)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("JSON control-char repair failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------
_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Return the shared LLM client (lazy init)."""
    global _client
    if _client is None:
        _client = GeminiClient()
    return _client
