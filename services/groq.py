"""Groq backup provider for itinerary generation, using its OpenAI-compatible REST API."""
import json
import logging
import re
import time
from typing import Optional

import httpx

import settings
from models import TripRequest
from services.gemini import build_prompt

log = logging.getLogger("travel_genie.groq")
API_URL = "https://api.groq.com/openai/v1/chat/completions"
INVALID_RESPONSE_KINDS = {"malformed", "empty", "blocked", "truncated"}
_KEY_LIKE = re.compile(r"(?i)(gsk_[0-9a-z_-]{12,}|AIza[0-9a-z_-]{20,})")
_KEY_PARAM = re.compile(r"(?i)\b(api[_-]?key|authorization|bearer|key)[:= ]+[^&\s\"']+")


class GroqError(Exception):
    """Groq provider failure with a safe message for logs and API metadata."""

    def __init__(self, message: str, kind: str = "request_failed", status_code: Optional[int] = None, detail: str = ""):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.detail = detail

    @property
    def category(self) -> str:
        if self.kind == "not_configured":
            return "not_configured"
        if self.kind in INVALID_RESPONSE_KINDS:
            return "invalid_response"
        return "request_failed"


def redact(text, max_len: int = 300) -> str:
    text = str(text or "")
    key = settings.groq_key()
    if key:
        text = text.replace(key, "[REDACTED]")
    text = _KEY_LIKE.sub("[REDACTED]", text)
    text = _KEY_PARAM.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    text = " ".join(text.split())
    return text if len(text) <= max_len else text[:max_len - 3] + "..."


def _fail(kind: str, message: str, status: Optional[int] = None, detail: str = "") -> GroqError:
    message, detail = redact(message, 500), redact(detail)
    category = "not_configured" if kind == "not_configured" else ("invalid_response" if kind in INVALID_RESPONSE_KINDS else "request_failed")
    log.warning("Groq failure: category=%s kind=%s http_status=%s message=%s detail=%s",
                category, kind, status if status is not None else "-", message, detail or "-")
    return GroqError(message, kind, status, detail)


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise _fail("empty", "Groq returned an empty response.")
    text = re.sub(r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$", "", text, flags=re.IGNORECASE)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise _fail("malformed", "Groq returned malformed JSON.")
        try:
            result = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            raise _fail("malformed", "Groq returned malformed JSON.")
    if not isinstance(result, dict):
        raise _fail("malformed", "Groq returned JSON in an unexpected shape.")
    return result


async def generate_itinerary(trip: TripRequest, research_context: str = "", client: Optional[httpx.AsyncClient] = None) -> dict:
    key = settings.groq_key()
    if not key:
        raise _fail("not_configured", "GROQ_API_KEY is not set.")
    model = settings.groq_model()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are Travel-Genie. Follow the user request and return only valid JSON."},
            {"role": "user", "content": build_prompt(trip, research_context)},
        ],
        "temperature": 0.7,
        "max_completion_tokens": settings.groq_max_output_tokens(),
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    timeout = settings.groq_timeout_seconds()
    log.info("Groq request: model=%s days=%s", model, trip.duration_days)
    started = time.monotonic()
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        try:
            resp = await client.post(API_URL, json=body, headers=headers)
        except httpx.TimeoutException as e:
            raise _fail("timeout", f"Groq did not answer within {int(timeout)} seconds", detail=type(e).__name__)
        except httpx.HTTPError as e:
            raise _fail("network", "Could not reach Groq (network or DNS problem)", detail=type(e).__name__)
    finally:
        if own_client:
            await client.aclose()
    elapsed_ms = int((time.monotonic() - started) * 1000)

    if resp.status_code != 200:
        try:
            payload = resp.json()
            err = payload.get("error", {})
            detail = str(err.get("message", err) if isinstance(err, dict) else err)
        except (ValueError, AttributeError):
            detail = (resp.text or "")[:300]
        status = resp.status_code
        kind = "rate_limit" if status == 429 else "invalid_key" if status in (401, 403) else "model_not_found" if status == 404 else "server" if status >= 500 else "http"
        labels = {
            "rate_limit": "Groq rate limit or quota reached",
            "invalid_key": "Groq rejected the API key or denied access. Check GROQ_API_KEY",
            "model_not_found": f"Groq model '{model}' was not found or is unavailable to this key",
            "server": "Groq had a server error",
            "http": "Groq returned an HTTP error",
        }
        raise _fail(kind, f"{labels[kind]} (HTTP {status})", status, detail)

    try:
        payload = resp.json()
        text = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise _fail("empty", "Groq returned no usable chat completion.", 200)
    result = _parse_json(text)
    log.info("Groq request succeeded: model=%s http_status=200 elapsed_ms=%d", model, elapsed_ms)
    return result
