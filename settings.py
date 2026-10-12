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

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def gemini_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL


def serpapi_key() -> str:
    return os.getenv("SERPAPI_API_KEY", "").strip()
