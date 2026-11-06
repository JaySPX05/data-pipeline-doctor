"""App settings, loaded from environment variables or the repo-root .env file."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ROOT_DIR / ".env"), ".env"), extra="ignore"
    )

    # LLM (Groq, OpenAI-compatible API)
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"
    llm_fallback_model: str = "openai/gpt-oss-20b"  # production model, separate rate-limit bucket
    max_tool_steps: int = 2          # changes + task history are prefetched, so tools are rarely needed
    llm_reasoning_effort: str = "low"  # gpt-oss only: fewer reasoning tokens = faster + cheaper
    llm_max_tokens: int = 2048       # completion cap (includes reasoning tokens)
    log_tail_lines: int = 40         # log lines sent to the model
    max_rate_limit_wait_s: float = 20.0  # longest we wait on a 429 Retry-After before falling back

    # Google Gemini (AI Studio) as a fallback provider, on its own quota. Empty key = off.
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_model: str = "gemini-3.5-flash"  # copy the exact id from AI Studio

    # Hindsight memory
    hindsight_url: str = "http://localhost:8888"
    hindsight_api_key: str = ""  # only needed for Hindsight Cloud
    hindsight_bank_id: str = "quickbasket-data-platform"
    memory_enabled: bool = True

    # Storage
    data_dir: Path = ROOT_DIR / "data"
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'pfd.sqlite3'}"

    # API
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


settings = Settings()
