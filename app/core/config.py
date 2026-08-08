"""Application settings loaded from environment variables / the ``.env`` file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """UniPulse AI application settings.

    Values are read from environment variables first, with a local ``.env``
    file (see ``.env.example``) used as the fallback source — the standard
    setup for local Windows development.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # --- App metadata ---------------------------------------------------
    app_name: str = "UniPulse AI"
    app_version: str = "0.1.0"

    # --- API keys -------------------------------------------------------
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None

    # --- Domain allow-list ----------------------------------------------
    # Provide as a JSON array in .env, e.g. ALLOWED_DOMAINS=["example.com"]
    allowed_domains: list[str] = []

    # --- CORS ------------------------------------------------------------
    # Origins allowed to call the API (defaults to "*" for development).
    cors_origins: list[str] = ["*"]


settings = Settings()
