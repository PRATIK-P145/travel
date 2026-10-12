"""Tests for bounded Gemini configuration settings."""
import settings


def test_gemini_timeout_uses_default_for_missing_or_invalid(monkeypatch):
    monkeypatch.delenv("GEMINI_TIMEOUT_SECONDS", raising=False)
    assert settings.gemini_timeout_seconds() == 35.0

    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "not-a-number")
    assert settings.gemini_timeout_seconds() == 35.0

    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "500")
    assert settings.gemini_timeout_seconds() == 35.0


def test_gemini_timeout_accepts_configured_value(monkeypatch):
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "45")
    assert settings.gemini_timeout_seconds() == 45.0


def test_gemini_token_limit_is_bounded(monkeypatch):
    monkeypatch.delenv("GEMINI_MAX_OUTPUT_TOKENS", raising=False)
    assert settings.gemini_max_output_tokens() == 8192

    monkeypatch.setenv("GEMINI_MAX_OUTPUT_TOKENS", "4096")
    assert settings.gemini_max_output_tokens() == 4096

    monkeypatch.setenv("GEMINI_MAX_OUTPUT_TOKENS", "99999")
    assert settings.gemini_max_output_tokens() == 8192

    monkeypatch.setenv("GEMINI_MAX_OUTPUT_TOKENS", "oops")
    assert settings.gemini_max_output_tokens() == 8192


def test_gemini_model_defaults_to_3_8_flash(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert settings.gemini_model() == "gemini-3.8-flash"
