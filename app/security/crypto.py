"""Credential encryption utilities.

Upstream IMAP/SMTP passwords are never stored in plaintext.
This module provides a symmetric encryption layer using Fernet,
which is an authenticated encryption scheme (AES-128-CBC + HMAC-SHA256).

Key management notes:
  - The data-encryption key is read from ENCRYPTION_KEY in the environment.
  - In future phases this can be extended to support a key-encryption-key (KEK)
    model or delegated to an on-premises HSM/Vault.

Usage:
    encryptor = get_encryptor()               # cached singleton from settings
    ciphertext = encryptor.encrypt("my-secret-password")
    plaintext  = encryptor.decrypt(ciphertext)
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


class CredentialEncryptor:
    """Encrypts and decrypts sensitive credential strings at rest.

    Thread-safe: Fernet instances are stateless after initialisation.
    """

    def __init__(self, key: str) -> None:
        """Initialise the encryptor with a Fernet key.

        Args:
            key: URL-safe base64-encoded 32-byte key (44 characters).

        Raises:
            ValueError: If the key is not a valid Fernet key.
        """
        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as exc:
            raise ValueError(
                "Invalid ENCRYPTION_KEY. "
                "Generate a valid key with: "
                "python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string.

        Returns:
            URL-safe base64-encoded ciphertext (includes auth tag and IV).
        """
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a ciphertext string produced by encrypt().

        Raises:
            ValueError: If the token is invalid or has been tampered with.
        """
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError(
                "Decryption failed: token is invalid, expired, or was encrypted "
                "with a different key."
            ) from exc


def generate_fernet_key() -> str:
    """Generate a new random Fernet key suitable for ENCRYPTION_KEY.

    Returns:
        A URL-safe base64 string (44 characters).
    """
    return Fernet.generate_key().decode("ascii")


@lru_cache(maxsize=1)
def get_encryptor() -> "CredentialEncryptor":
    """Return the application-wide CredentialEncryptor, cached after first call.

    Reads the encryption key from get_settings() so the encryptor is always
    consistent with the running application configuration.

    Cache is shared across requests — Fernet is stateless and thread-safe.
    """
    from app.config import get_settings  # local import prevents circular deps

    return CredentialEncryptor(get_settings().encryption_key)
