from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    supabase_db_url: str = ""

    session_secret: str = "dev-secret-change-me"
    token_encryption_key: str = ""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_min_interval_seconds: float = 13.0
    gemini_max_calls_per_sync: int = 4
    gemini_max_retries: int = 3

    cron_secret: str = ""
    ghosted_after_days: int = 21

    gmail_sync_days: int = 90

    @property
    def google_scopes(self) -> list[str]:
        return [
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.readonly",
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
