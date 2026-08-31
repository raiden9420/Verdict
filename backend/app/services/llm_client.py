"""
LLM client abstraction.

Phase 1: Gemini-only implementation.
Phase 2: Multi-provider router adding Groq and OpenRouter with automatic fallback.
"""

import json
import math
import re
import time
import ssl
import logging
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from typing import Any, TypeVar

import certifi
import google.genai as genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.config import GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY
from app.constants import GEMINI_MODEL, LLM_MAX_RETRIES, LLM_BASE_DELAY_SECONDS

logger = logging.getLogger(__name__)

AgentSchema = TypeVar("AgentSchema", bound=BaseModel)
SCHEMA_VALIDATION_RETRIES = 3
_TRANSIENT_HTTP_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_MAX_PROVIDER_RETRY_DELAY_SECONDS = 60
_RETRY_AFTER_DURATION_RE = re.compile(
    r"try\s+again\s+in\s+(?:(?P<minutes>\d+(?:\.\d+)?)m)?"
    r"(?P<seconds>\d+(?:\.\d+)?)s",
    re.IGNORECASE,
)
_RETRY_AFTER_MILLISECONDS_RE = re.compile(
    r"try\s+again\s+in\s+(?P<milliseconds>\d+(?:\.\d+)?)ms",
    re.IGNORECASE,
)

# Verified SSL context using certifi CA bundle
ssl_ctx = ssl.create_default_context(cafile=certifi.where())


def _retry_delay(attempt: int) -> int:
    """Bounded exponential delay; ``attempt`` is zero-indexed."""
    return min(30, LLM_BASE_DELAY_SECONDS * (2**attempt))


def _provider_retry_delay(exc: urllib.error.HTTPError, body: str) -> int | None:
    """Return a bounded provider-requested retry delay, when one is supplied."""
    candidates: list[float] = []
    headers = getattr(exc, "headers", None)
    retry_after = headers.get("Retry-After") if headers is not None else None
    if retry_after is not None:
        try:
            candidates.append(float(retry_after))
        except (TypeError, ValueError):
            # HTTP-date Retry-After values are deliberately ignored; the normal
            # exponential delay remains safe and avoids wall-clock assumptions.
            pass

    duration_match = _RETRY_AFTER_DURATION_RE.search(body)
    if duration_match:
        minutes = float(duration_match.group("minutes") or 0)
        seconds = float(duration_match.group("seconds"))
        candidates.append(minutes * 60 + seconds)
    else:
        millisecond_match = _RETRY_AFTER_MILLISECONDS_RE.search(body)
        if millisecond_match:
            candidates.append(float(millisecond_match.group("milliseconds")) / 1000)

    if not candidates:
        return None
    return max(1, min(_MAX_PROVIDER_RETRY_DELAY_SECONDS, math.ceil(max(candidates))))


def _is_transient_provider_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_HTTP_CODES
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return True
    error = str(exc).lower()
    return any(
        marker in error
        for marker in (
            "408",
            "409",
            "425",
            "429",
            "500",
            "502",
            "503",
            "504",
            "deadline",
            "resource exhausted",
            "rate limit",
            "quota",
            "temporarily unavailable",
            "timeout",
            "timed out",
            "connection reset",
            "connection aborted",
        )
    )


def _generate_openai_compatible(
    *,
    provider_name: str,
    endpoint: str,
    headers: dict[str, str],
    payload: dict,
) -> dict:
    """Call an OpenAI-compatible endpoint with bounded transient retries."""
    last_exc: Exception | None = None
    data_bytes = json.dumps(payload).encode("utf-8")

    for attempt in range(LLM_MAX_RETRIES):
        provider_retry_delay: int | None = None
        try:
            request = urllib.request.Request(
                endpoint,
                data=data_bytes,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(
                request,
                timeout=30.0,
                context=ssl_ctx,
            ) as response:
                response_json = json.loads(response.read().decode("utf-8"))
                raw = response_json["choices"][0]["message"]["content"].strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[-1]
                    raw = raw.rsplit("```", 1)[0]
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    repaired = _repair_json(raw)
                    if repaired is not None:
                        logger.info(
                            "JSON repair succeeded for %s on attempt %d",
                            provider_name,
                            attempt + 1,
                        )
                        return repaired
                    last_exc = exc
                    logger.warning(
                        "%s returned malformed JSON (attempt %d/%d)",
                        provider_name,
                        attempt + 1,
                        LLM_MAX_RETRIES,
                    )
                    continue
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            last_exc = RuntimeError(f"{provider_name} API error {exc.code}: {body}")
            logger.warning("%s API HTTP error %d: %s", provider_name, exc.code, body)
            if not _is_transient_provider_error(exc):
                raise last_exc from exc
            provider_retry_delay = _provider_retry_delay(exc, body)
        except Exception as exc:
            last_exc = exc
            if not _is_transient_provider_error(exc) and not isinstance(
                exc,
                (json.JSONDecodeError, KeyError, IndexError, TypeError),
            ):
                raise RuntimeError(f"{provider_name} API call failed: {exc}") from exc
            logger.warning(
                "Transient or malformed %s response (attempt %d/%d): %s",
                provider_name,
                attempt + 1,
                LLM_MAX_RETRIES,
                exc,
            )

        if attempt < LLM_MAX_RETRIES - 1:
            delay = max(_retry_delay(attempt), provider_retry_delay or 0)
            logger.info(
                "Retrying %s in %ds (attempt %d/%d)",
                provider_name,
                delay,
                attempt + 2,
                LLM_MAX_RETRIES,
            )
            time.sleep(delay)

    raise RuntimeError(
        f"{provider_name} LLM call failed after {LLM_MAX_RETRIES} attempts: {last_exc}"
    ) from last_exc



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
                    parsed = json.loads(raw)
                    logger.info("Gemini LLM generation succeeded using model '%s'", self.model_name)
                    return parsed
                except json.JSONDecodeError:
                    repaired = _repair_json(raw)
                    if repaired is not None:
                        logger.info("Gemini LLM generation succeeded (JSON repaired) using model '%s' on attempt %d", self.model_name, json_failures + 1)
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
                if _is_transient_provider_error(exc):
                    api_failures += 1
                    delay = _retry_delay(api_failures - 1)
                    logger.warning(
                        "Transient Gemini failure (api attempt %d/%d), "
                        "retrying in %ds: %s",
                        api_failures,
                        MAX_API_RETRIES,
                        delay,
                        exc,
                    )
                    if api_failures < MAX_API_RETRIES:
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

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        response_format: dict | None = None,
        max_completion_tokens: int | None = None,
        reasoning_effort: str | None = None,
        reasoning_format: str | None = None,
    ) -> None:
        self.api_key = api_key or GROQ_API_KEY
        self.model_name = model_name or "llama-3.3-70b-versatile"
        self.response_format = response_format or {"type": "json_object"}
        self.max_completion_tokens = max_completion_tokens
        self.reasoning_effort = reasoning_effort
        self.reasoning_format = reasoning_format
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
            "response_format": self.response_format,
            "temperature": 0.4,
        }
        if self.max_completion_tokens is not None:
            payload["max_completion_tokens"] = self.max_completion_tokens
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.reasoning_format is not None:
            payload["reasoning_format"] = self.reasoning_format

        return _generate_openai_compatible(
            provider_name="Groq",
            endpoint=self.endpoint,
            headers=headers,
            payload=payload,
        )


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

        return _generate_openai_compatible(
            provider_name="OpenRouter",
            endpoint=self.endpoint,
            headers=headers,
            payload=payload,
        )


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

    def generate_with_meta(
        self,
        system_prompt: str,
        user_prompt: str,
        pinned_client: LLMClient | None = None,
    ) -> tuple[dict, LLMClient, str]:
        """
        Generate content and return (result_dict, serving_client_instance, provider_name).
        If pinned_client is passed, only that client instance is used.
        """
        if pinned_client is not None:
            name = getattr(pinned_client, "model_name", "PinnedProvider")
            logger.info("Executing pinned LLM generation via client '%s'", name)
            res = pinned_client.generate(system_prompt, user_prompt)
            return res, pinned_client, str(name)

        if not self.providers:
            raise RuntimeError("No LLM provider is available or configured.")

        last_error: Exception | None = None
        for name, client in self.providers:
            try:
                logger.info("Attempting LLM generation via provider '%s'", name)
                res = client.generate(system_prompt, user_prompt)
                return res, client, name
            except Exception as exc:
                last_error = exc
                logger.warning("Provider '%s' failed: %s. Trying next provider...", name, exc)

        raise RuntimeError(f"All configured LLM providers failed. Last error: {last_error}")

    def generate(self, system_prompt: str, user_prompt: str) -> dict:
        res, _, _ = self.generate_with_meta(system_prompt, user_prompt)
        return res


def generate_structured_with_meta(
    client: LLMClient,
    system_prompt: str,
    user_prompt: str,
    output_schema: type[AgentSchema],
    *,
    pinned_client: LLMClient | None = None,
    max_schema_attempts: int = SCHEMA_VALIDATION_RETRIES,
) -> tuple[dict, LLMClient, str]:
    """Generate and strictly validate a JSON object against ``output_schema``.

    JSON syntax alone is not an agent contract.  This helper retries the model
    when types, required fields, enum values, UUIDs, or numeric ranges are
    invalid.  When ``pinned_client`` is supplied (the Referee consistency
    check), every retry stays on that exact provider instance.
    """
    if max_schema_attempts < 1:
        raise ValueError("max_schema_attempts must be at least 1")

    prompt = user_prompt
    last_error: Exception | None = None

    for attempt in range(1, max_schema_attempts + 1):
        if pinned_client is not None:
            if isinstance(client, MultiProviderLLMClient):
                raw, serving_client, provider_name = client.generate_with_meta(
                    system_prompt,
                    prompt,
                    pinned_client=pinned_client,
                )
            else:
                raw = pinned_client.generate(system_prompt, prompt)
                serving_client = pinned_client
                provider_name = str(getattr(pinned_client, "model_name", type(pinned_client).__name__))
        elif isinstance(client, MultiProviderLLMClient):
            raw, serving_client, provider_name = client.generate_with_meta(system_prompt, prompt)
        else:
            raw = client.generate(system_prompt, prompt)
            serving_client = client
            provider_name = str(getattr(client, "model_name", type(client).__name__))

        try:
            if not isinstance(raw, dict):
                raise TypeError(f"expected a JSON object, received {type(raw).__name__}")
            validated = output_schema.model_validate(raw)
            return validated.model_dump(mode="json"), serving_client, provider_name
        except (ValidationError, TypeError) as exc:
            last_error = exc
            logger.warning(
                "%s schema validation failed on attempt %d/%d via %s: %s",
                output_schema.__name__,
                attempt,
                max_schema_attempts,
                provider_name,
                exc,
            )
            if attempt < max_schema_attempts:
                if isinstance(exc, ValidationError):
                    details = json.dumps(exc.errors(include_url=False), default=str)
                else:
                    details = str(exc)
                prompt = (
                    user_prompt
                    + "\n\n## Response correction required\n"
                    + "Your previous JSON response failed the required schema. "
                    + "Return a corrected JSON object only. Validation errors: "
                    + details
                )

    raise RuntimeError(
        f"{output_schema.__name__} generation failed schema validation after "
        f"{max_schema_attempts} attempts: {last_error}"
    ) from last_error


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
