"""Unit tests for app.config.Settings.

Validates that settings load correctly from environment variables,
that required fields are enforced, and that validators reject bad input.
"""

import os

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_FERNET_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_VALID_SECRET_KEY = "a" * 32


def _make_settings(**overrides):
    """Instantiate Settings with a baseline of valid test values plus overrides."""
    from app.config import Settings

    defaults = {
        "secret_key": _VALID_SECRET_KEY,
        "encryption_key": _VALID_FERNET_KEY,
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
        "redis_url": "redis://localhost:6379/0",
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestSettingsLoading:
    def test_defaults_are_applied(self):
        s = _make_settings()
        assert s.app_name == "Mindwall"
        assert s.debug is False
        assert s.ollama_model == "llama3.1:8b"
        assert s.imap_proxy_port == 1993
        assert s.smtp_proxy_port == 1587
        assert s.analysis_enabled is True
        assert s.gateway_mode is False

    def test_overrides_are_respected(self):
        s = _make_settings(app_name="TestWall", debug=True, gateway_mode=True)
        assert s.app_name == "TestWall"
        assert s.debug is True
        assert s.gateway_mode is True

    def test_sync_database_url_substitution(self):
        s = _make_settings(
            database_url="postgresql+asyncpg://user:pass@db-host:5432/mydb"
        )
        sync_url = s.sync_database_url
        assert "psycopg2" in sync_url
        assert "asyncpg" not in sync_url
        assert "user:pass@db-host:5432/mydb" in sync_url

    def test_blob_storage_path_is_a_path(self):
        from pathlib import Path

        s = _make_settings()
        assert isinstance(s.blob_storage_path, Path)


# ---------------------------------------------------------------------------
# Validation failure tests
# ---------------------------------------------------------------------------


class TestSettingsValidation:
    def test_secret_key_too_short_raises(self):
        with pytest.raises(ValidationError, match="min_length"):
            _make_settings(secret_key="short")

    def test_invalid_encryption_key_raises(self):
        with pytest.raises(ValidationError, match="ENCRYPTION_KEY"):
            _make_settings(encryption_key="not-a-fernet-key")

    def test_missing_secret_key_raises(self):
        from app.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                encryption_key=_VALID_FERNET_KEY,
                database_url="postgresql+asyncpg://u:p@localhost/db",
                redis_url="redis://localhost:6379/0",
                # secret_key intentionally omitted
            )

    def test_missing_encryption_key_raises(self):
        from app.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                secret_key=_VALID_SECRET_KEY,
                database_url="postgresql+asyncpg://u:p@localhost/db",
                redis_url="redis://localhost:6379/0",
                # encryption_key intentionally omitted
            )


# ---------------------------------------------------------------------------
# Encryption key validator test
# ---------------------------------------------------------------------------


class TestEncryptionKeyValidator:
    def test_valid_fernet_key_passes(self):
        from cryptography.fernet import Fernet

        real_key = Fernet.generate_key().decode()
        s = _make_settings(encryption_key=real_key)
        assert s.encryption_key == real_key

    def test_all_zeros_key_passes(self):
        """The all-zeros key is structurally valid for tests."""
        s = _make_settings(encryption_key=_VALID_FERNET_KEY)
        assert s.encryption_key == _VALID_FERNET_KEY
