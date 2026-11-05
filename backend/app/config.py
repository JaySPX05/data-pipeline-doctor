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
    llm_fallback_model: str = "qwen/qwen3-32b"
    max_tool_steps: int = 4

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
