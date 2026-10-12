"""Unit tests for Groq response parsing and safe error handling."""
import pytest

import settings
from services import groq


def test_groq_parses_json_and_fenced_json():
    assert groq._parse_json('{"overview":"A trip","days":[]}') == {
        "overview": "A trip",
        "days": [],
    }
    assert groq._parse_json('```json\n{"overview":"A trip"}\n```') == {
        "overview": "A trip",
    }


def test_groq_rejects_malformed_json():
    with pytest.raises(groq.GroqError) as exc:
        groq._parse_json("not JSON")
    assert exc.value.kind == "malformed"
    assert "Groq" in str(exc.value)


def test_groq_error_redacts_configured_key(monkeypatch):
    fake_key = "gsk_test_key_1234567890abcdef"
    monkeypatch.setenv("GROQ_API_KEY", fake_key)
    safe = groq.redact(f"provider returned {fake_key}")
    assert fake_key not in safe
    assert "[REDACTED]" in safe
