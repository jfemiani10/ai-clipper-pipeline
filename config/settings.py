from pathlib import Path
from dotenv import load_dotenv
import os

# Load .env from the project root (one level up from config/)
_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable '{name}' is not set. Check your .env file.")
    return value


def _get(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_path(name: str, default: str) -> Path:
    return _PROJECT_ROOT / os.getenv(name, default)


# --- API Keys ---
ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")

# --- Whisper ---
WHISPER_MODEL: str = _get("WHISPER_MODEL", "base")
WHISPER_LANGUAGE: str = _get("WHISPER_LANGUAGE", "en")

# --- Clip scoring ---
MIN_CLIP_SCORE: float = float(_get("MIN_CLIP_SCORE", "0.7"))

# --- Clip timing ---
CLIP_BUFFER_SECONDS: float = float(_get("CLIP_BUFFER_SECONDS", "2"))
MAX_CLIP_DURATION: float = float(_get("MAX_CLIP_DURATION", "120"))

# --- Data directories ---
DOWNLOAD_DIR: Path = _get_path("DOWNLOAD_DIR", "data/downloads")
TRANSCRIPT_DIR: Path = _get_path("TRANSCRIPT_DIR", "data/transcripts")
CLIPS_DIR: Path = _get_path("CLIPS_DIR", "data/clips")
EXPORT_DIR: Path = _get_path("EXPORT_DIR", "data/exports")

# --- Infrastructure ---
REDIS_URL: str = _get("REDIS_URL", "redis://localhost:6379")
DATABASE_PATH: Path = _get_path("DATABASE_PATH", "data/jobs.db")

# --- Logging ---
LOG_LEVEL: str = _get("LOG_LEVEL", "INFO")

# --- Claude model ---
CLAUDE_MODEL: str = _get("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_MAX_TOKENS: int = int(_get("CLAUDE_MAX_TOKENS", "1024"))

# --- Prompts ---
PROMPT_DIR: Path = _PROJECT_ROOT / "config" / "prompts"

# --- YouTube upload ---
YOUTUBE_CLIENT_SECRETS: Path = _PROJECT_ROOT / "config" / "youtube_client_secrets.json"
YOUTUBE_TOKEN_PATH: Path = _PROJECT_ROOT / "config" / "youtube_token.json"
YOUTUBE_ENABLED: bool = (_PROJECT_ROOT / "config" / "youtube_token.json").exists()

# --- Clip retention ---
CLIP_RETENTION_DAYS: int = int(_get("CLIP_RETENTION_DAYS", "7"))
