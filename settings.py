"""Environment-based configuration. Keys are read at call time so tests can override them."""
import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
# Load .env from the project folder (not the current working directory).
# override=True so a blank GEMINI_API_KEY already set in your shell can't hide the file's value.
load_dotenv(ENV_PATH, override=True)


def env_file_found() -> bool:
    return ENV_PATH.is_file()

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


def gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL


def serpapi_key() -> str:
    return os.getenv("SERPAPI_API_KEY", "").strip()


DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


def groq_key() -> str:
    return os.getenv("GROQ_API_KEY", "").strip()


def groq_model() -> str:
    return os.getenv("GROQ_MODEL", "").strip() or DEFAULT_GROQ_MODEL


def _positive_number(name: str, default: float, minimum: float, maximum: float) -> float:
    """Read a bounded positive numeric setting, falling back safely on bad input."""
    try:
        value = float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default
    return value if minimum <= value <= maximum else default


def gemini_timeout_seconds() -> float:
    """HTTP timeout for Gemini, configurable from .env (10–120 seconds)."""
    return _positive_number("GEMINI_TIMEOUT_SECONDS", 35.0, 10.0, 120.0)


def gemini_max_output_tokens() -> int:
    """Output token cap for Gemini, configurable from .env (1024–8192 tokens)."""
    try:
        value = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "8192").strip())
    except (TypeError, ValueError):
        return 8192
    return value if 1024 <= value <= 8192 else 8192


def groq_timeout_seconds() -> float:
    """HTTP timeout for Groq, configurable from .env (10–120 seconds)."""
    return _positive_number("GROQ_TIMEOUT_SECONDS", 35.0, 10.0, 120.0)


def groq_max_output_tokens() -> int:
    """Output token cap for Groq, configurable from .env (1024–16384 tokens)."""
    try:
        value = int(os.getenv("GROQ_MAX_OUTPUT_TOKENS", "8192").strip())
    except (TypeError, ValueError):
        return 8192
    return value if 1024 <= value <= 16384 else 8192
