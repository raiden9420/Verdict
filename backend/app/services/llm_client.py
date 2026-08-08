"""
LLM client abstraction.

Phase 1: Gemini-only implementation.
Phase 2: Multi-provider router adding Groq and OpenRouter with automatic fallback.
"""

import json
import time
import ssl
import logging
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from typing import Any

import google.genai as genai
from google.genai import types

from app.config import GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY
from app.constants import GEMINI_MODEL, LLM_MAX_RETRIES, LLM_BASE_DELAY_SECONDS

logger = logging.getLogger(__name__)

# Permissive SSL context for urllib calls on macOS
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE



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
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not configured")

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            temperature=0.4,
        )

        MAX_JSON_RETRIES = 3   # separate budget for malformed JSON
        MAX_API_RETRIES = LLM_MAX_RETRIES

        last_exc: Exception | None = None
        json_failures = 0
        api_failures = 0

        while (json_failures < MAX_JSON_RETRIES) and (api_failures < MAX_API_RETRIES):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=user_prompt,
                    config=config,
                )
                raw = response.text.strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[-1]  # drop opening fence
                    raw = raw.rsplit("```", 1)[0]  # drop closing fence

                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    repaired = _repair_json(raw)
                    if repaired is not None:
                        logger.info("JSON repair succeeded for Gemini on attempt %d", json_failures + 1)
                        return repaired
                    json_failures += 1
                    last_exc = json.JSONDecodeError("malformed", raw[:100], 0)
                    logger.warning(
                        "Gemini JSON parse+repair failed (attempt %d/%d) — retrying LLM call …",
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
                    delay = min(30, LLM_BASE_DELAY_SECONDS * (2 ** api_failures))
                    logger.warning(
                        "Gemini Rate-limited (api attempt %d/%d), retrying in %ds …",
                        api_failures,
                        MAX_API_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error("Gemini call failed (non-retryable): %s", exc)
                    raise

        raise RuntimeError(
            f"Gemini call failed after {json_failures} JSON retries + {api_failures} API retries: {last_exc}"
        )


class GroqClient(LLMClient):
    """
    Groq API Client (OpenAI compatible REST endpoint).
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or GROQ_API_KEY
        self.model_name = "llama-3.3-70b-versatile"
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def generate(self, system_prompt: str, user_prompt: str) -> dict:
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.4,
        }

        MAX_JSON_RETRIES = 3
        last_exc: Exception | None = None

        for attempt in range(MAX_JSON_RETRIES):
            try:
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(self.endpoint, data=data_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=30.0, context=ssl_ctx) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    raw = res_json["choices"][0]["message"]["content"].strip()
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[-1]
                        raw = raw.rsplit("```", 1)[0]
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        repaired = _repair_json(raw)
                        if repaired is not None:
                            logger.info("JSON repair succeeded for Groq on attempt %d", attempt + 1)
                            return repaired
                        last_exc = json.JSONDecodeError("malformed", raw[:100], 0)
            except urllib.error.HTTPError as http_err:
                err_body = http_err.read().decode("utf-8", errors="ignore")
                logger.warning("Groq API HTTP error %d: %s", http_err.code, err_body)
                raise RuntimeError(f"Groq API error {http_err.code}: {err_body}") from http_err
            except Exception as exc:
                last_exc = exc
                logger.warning("Groq API call failed (attempt %d): %s", attempt + 1, exc)

        raise RuntimeError(f"Groq LLM call failed: {last_exc}")


class OpenRouterClient(LLMClient):
    """
    OpenRouter API Client (OpenAI compatible REST endpoint).
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or OPENROUTER_API_KEY
        self.model_name = "meta-llama/llama-3.3-70b-instruct"
        self.endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def generate(self, system_prompt: str, user_prompt: str) -> dict:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "HTTP-Referer": "https://verdict.ai",
            "X-Title": "Verdict Academic Audit",
        }

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.4,
        }

        MAX_JSON_RETRIES = 3
        last_exc: Exception | None = None

        for attempt in range(MAX_JSON_RETRIES):
            try:
                data_bytes = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(self.endpoint, data=data_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=30.0, context=ssl_ctx) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    raw = res_json["choices"][0]["message"]["content"].strip()
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[-1]
                        raw = raw.rsplit("```", 1)[0]
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        repaired = _repair_json(raw)
                        if repaired is not None:
                            logger.info("JSON repair succeeded for OpenRouter on attempt %d", attempt + 1)
                            return repaired
                        last_exc = json.JSONDecodeError("malformed", raw[:100], 0)
            except urllib.error.HTTPError as http_err:
                err_body = http_err.read().decode("utf-8", errors="ignore")
                logger.warning("OpenRouter API HTTP error %d: %s", http_err.code, err_body)
                raise RuntimeError(f"OpenRouter API error {http_err.code}: {err_body}") from http_err
            except Exception as exc:
                last_exc = exc
                logger.warning("OpenRouter API call failed (attempt %d): %s", attempt + 1, exc)

        raise RuntimeError(f"OpenRouter LLM call failed: {last_exc}")


class MultiProviderLLMClient(LLMClient):
    """
    Multi-provider LLM client with automatic fallback:
    Order: Gemini -> Groq -> OpenRouter.
    Skips unconfigured providers (missing API key).
    """

    def __init__(self) -> None:
        self.providers: list[tuple[str, LLMClient]] = []

        if GEMINI_API_KEY:
            try:
                self.providers.append(("Gemini", GeminiClient()))
            except Exception as exc:
                logger.warning("Failed to init Gemini client: %s", exc)

        if GROQ_API_KEY:
            try:
                self.providers.append(("Groq", GroqClient()))
            except Exception as exc:
                logger.warning("Failed to init Groq client: %s", exc)

        if OPENROUTER_API_KEY:
            try:
                self.providers.append(("OpenRouter", OpenRouterClient()))
            except Exception as exc:
                logger.warning("Failed to init OpenRouter client: %s", exc)

        if not self.providers:
            logger.error("No LLM providers configured!")

    def generate(self, system_prompt: str, user_prompt: str) -> dict:
        if not self.providers:
            raise RuntimeError("No LLM provider is available or configured.")

        last_error: Exception | None = None
        for name, client in self.providers:
            try:
                logger.info("Attempting LLM generation via provider '%s'", name)
                return client.generate(system_prompt, user_prompt)
            except Exception as exc:
                last_error = exc
                logger.warning("Provider '%s' failed: %s. Trying next provider...", name, exc)

        raise RuntimeError(f"All configured LLM providers failed. Last error: {last_error}")


def _repair_json(raw: str) -> dict | None:
    """
    Best-effort repair of common JSON issues from LLM output:
    - Trailing commas before } or ]
    - Unescaped control characters inside strings
    - Smart quotes → straight quotes
    - Truncated JSON (add missing closing braces)
    - Extract JSON object if surrounded by extra text
    """
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
        fixed = raw.replace("\u201c", '"').replace("\u201d", '"')
        fixed = fixed.replace("\u2018", "'").replace("\u2019", "'")
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)

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
        fixed = raw.replace("\u201c", '"').replace("\u201d", '"')
        fixed = fixed.replace("\u2018", "'").replace("\u2019", "'")
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)

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
                result.append(f"\\u{ord(ch):04x}")
                continue
            result.append(ch)

        fixed = "".join(result)

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
        _client = MultiProviderLLMClient()
    return _client
