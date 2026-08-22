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
        extra="ignore",
    )

    # --- App metadata ---------------------------------------------------
    app_name: str = "UniPulse AI"
    app_version: str = "0.1.0"

    # --- API keys -------------------------------------------------------
    openai_api_key: str | None = None
    groq_api_key: str | None = None
    #: Comma-separated list of Groq keys used for multi-key sharding / rotation
    #: (one key per shard, automatic 429 failover). Falls back to
    #: ``GROQ_API_KEY`` when empty/unset.
    groq_api_keys: str | None = None

    @property
    def groq_api_key_list(self) -> list[str]:
        """Groq API keys as a clean list.

        Splits ``GROQ_API_KEYS`` on commas and strips surrounding quotes and
        whitespace from every entry (``'" gsk_a , gsk_b "'`` ->
        ``["gsk_a", "gsk_b"]``). When ``GROQ_API_KEYS`` yields no usable
        keys (empty, unset, or only commas/whitespace), falls back gracefully
        to ``GROQ_API_KEY``.
        """
        keys = self._parse_keys(self.groq_api_keys)
        if not keys:
            keys = self._parse_keys(self.groq_api_key)
        return keys

    @staticmethod
    def _parse_keys(raw: str | None) -> list[str]:
        """Split *raw* on commas, stripping quotes/whitespace per entry."""
        keys: list[str] = []
        for key in (raw or "").split(","):
            key = key.strip().strip('"').strip("'").strip()
            if key:
                keys.append(key)
        return keys

    # --- LLM Model Identifiers ------------------------------------------
    primary_model: str = "openai/gpt-oss-20b"
    fallback_model: str = "llama-3.1-70b-versatile"
    secondary_fallback_model: str = "llama-3.1-8b-instant"

    # --- Database / Persistence -----------------------------------------
    database_path: str = "unipulse_checkpoint.db"

    # --- Domain allow-list ----------------------------------------------
    # Provide as a JSON array in .env, e.g. ALLOWED_DOMAINS=["example.com"]
    allowed_domains: list[str] = []

    # --- CORS ------------------------------------------------------------
    # Origins allowed to call the API (defaults to "*" for development).
    cors_origins: list[str] = ["*"]


settings = Settings()
