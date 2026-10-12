"""Environment-based configuration. Keys are read at call time so tests can override them."""
import os

from dotenv import load_dotenv

load_dotenv()  # loads .env if present; real environment variables take precedence

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL


def serpapi_key() -> str:
    return os.getenv("SERPAPI_API_KEY", "").strip()
