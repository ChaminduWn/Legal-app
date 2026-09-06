from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Sri Lankan Legal AI"
    gemini_api_key: str | None = None
    gemini_chat_model: str = "gemini-flash-latest"
    gemini_embedding_model: str = "gemini-embedding-001"
    legal_ai_database: Path = Path("data/legal_index.sqlite3")
    legal_ai_top_k: int = 6
    legal_ai_embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    legal_ai_embedding_cache: Path = Path("data/models")
    admin_username: str = "admin"
    admin_password: str = "change-this-password"
    admin_session_secret: str = "change-this-session-secret"
    admin_session_hours: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()
