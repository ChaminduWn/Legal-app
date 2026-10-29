from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "production"] = "development"
    app_name: str = "Sri Lankan Legal AI"
    gemini_api_key: str | None = None
    database_url: str | None = None
    gemini_chat_model: str = "gemini-flash-latest"
    gemini_embedding_model: str = "gemini-embedding-001"
    legal_ai_database: Path = Path("data/legal_index.sqlite3")
    legal_ai_storage_root: Path = Path("data/storage")
    legal_ai_top_k: int = 6
    legal_ai_embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    legal_ai_embedding_cache: Path = Path("data/models")
    admin_username: str = "admin"
    admin_password: str = "change-this-password"
    admin_session_secret: str = "change-this-session-secret"
    admin_session_hours: int = 8

    @model_validator(mode="after")
    def production_credentials(self):
        if self.environment == "production":
            if self.admin_password == "change-this-password" or len(self.admin_password) < 16:
                raise ValueError("Production requires an admin password of at least 16 characters.")
            if self.admin_session_secret == "change-this-session-secret" or len(self.admin_session_secret) < 32:
                raise ValueError("Production requires a random session secret of at least 32 characters.")
            if not self.database_url:
                if not self.legal_ai_storage_root.is_absolute() or not self.legal_ai_database.is_absolute():
                    raise ValueError("Production requires absolute database and private storage paths on persistent volumes.")
                project_root = Path(__file__).resolve().parent.parent
                if self.legal_ai_storage_root.resolve().is_relative_to(project_root) or self.legal_ai_database.resolve().is_relative_to(project_root):
                    raise ValueError("Production database and originals must be outside the code checkout.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
