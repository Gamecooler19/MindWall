"""Typed, environment-variable-driven configuration for Mindwall.

All settings are read once and cached via get_settings().
No os.getenv() calls should appear elsewhere in the codebase.
"""

from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -----------------------------------------------------------------------
    # Application
    # -----------------------------------------------------------------------
    app_name: str = "Mindwall"
    debug: bool = False

    # Must be at least 32 characters — used to sign session cookies.
    secret_key: str = Field(..., min_length=32)

    # -----------------------------------------------------------------------
    # Database
    # -----------------------------------------------------------------------
    # Use postgresql+asyncpg:// scheme for async SQLAlchemy.
    database_url: str = Field(
        default="postgresql+asyncpg://mindwall:mindwall@localhost:5432/mindwall"
    )

    # -----------------------------------------------------------------------
    # Redis
    # -----------------------------------------------------------------------
    redis_url: str = Field(default="redis://localhost:6379/0")

    # -----------------------------------------------------------------------
    # Session
    # -----------------------------------------------------------------------
    # Session cookie lifetime in seconds.
    session_max_age: int = 3600

    # -----------------------------------------------------------------------
    # Encryption
    # -----------------------------------------------------------------------
    # Fernet key used to encrypt upstream IMAP/SMTP credentials at rest.
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key())"
    encryption_key: str = Field(...)

    # -----------------------------------------------------------------------
    # Ollama — local LLM inference (no cloud APIs)
    # -----------------------------------------------------------------------
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    # -----------------------------------------------------------------------
    # Proxy listeners
    # -----------------------------------------------------------------------
    imap_proxy_host: str = "0.0.0.0"  # noqa: S104 — intentional bind-all for proxy listener
    imap_proxy_port: int = 1993
    smtp_proxy_host: str = "0.0.0.0"  # noqa: S104 — intentional bind-all for proxy listener
    smtp_proxy_port: int = 1587

    # Human-readable hostnames shown in proxy setup instructions.
    # Change these to the hostname/IP that mail clients will use to reach
    # the Mindwall proxy. Default to localhost for single-node deployments.
    imap_proxy_display_host: str = "127.0.0.1"
    smtp_proxy_display_host: str = "127.0.0.1"

    # Seconds before upstream connectivity checks time out.
    connection_timeout_seconds: int = 10

    # -----------------------------------------------------------------------
    # Storage
    # -----------------------------------------------------------------------
    blob_storage_path: Path = Path("./data/blobs")

    # -----------------------------------------------------------------------
    # Feature flags
    # -----------------------------------------------------------------------
    analysis_enabled: bool = True
    gateway_mode: bool = False

    # -----------------------------------------------------------------------
    # Validators
    # -----------------------------------------------------------------------

    @field_validator("encryption_key")
    @classmethod
    def validate_encryption_key(cls, v: str) -> str:
        """Validate that the encryption key is a well-formed Fernet key."""
        try:
            Fernet(v.encode())
        except Exception as exc:
            raise ValueError(
                "ENCRYPTION_KEY must be a valid Fernet key (URL-safe base64, 44 chars). "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            ) from exc
        return v

    # -----------------------------------------------------------------------
    # Computed properties
    # -----------------------------------------------------------------------

    @property
    def sync_database_url(self) -> str:
        """Return a synchronous URL for Alembic migrations (psycopg2 driver)."""
        return self.database_url.replace(
            "postgresql+asyncpg://", "postgresql+psycopg2://"
        )


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()
