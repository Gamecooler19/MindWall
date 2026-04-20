"""Unit tests for app.security.crypto."""

import pytest
from app.security.crypto import CredentialEncryptor, generate_fernet_key
from cryptography.fernet import Fernet


class TestCredentialEncryptor:
    @pytest.fixture
    def key(self) -> str:
        return Fernet.generate_key().decode()

    @pytest.fixture
    def encryptor(self, key) -> CredentialEncryptor:
        return CredentialEncryptor(key)

    def test_encrypt_returns_string(self, encryptor):
        ct = encryptor.encrypt("super-secret-password")
        assert isinstance(ct, str)
        assert len(ct) > 0

    def test_encrypt_decrypt_roundtrip(self, encryptor):
        plaintext = "upstream-imap-password-123"
        assert encryptor.decrypt(encryptor.encrypt(plaintext)) == plaintext

    def test_ciphertext_is_not_plaintext(self, encryptor):
        plaintext = "my-secret"
        ct = encryptor.encrypt(plaintext)
        assert plaintext not in ct

    def test_wrong_key_raises_value_error(self, key):
        other_key = Fernet.generate_key().decode()
        encryptor_a = CredentialEncryptor(key)
        encryptor_b = CredentialEncryptor(other_key)

        ct = encryptor_a.encrypt("data")
        with pytest.raises(ValueError, match="Decryption failed"):
            encryptor_b.decrypt(ct)

    def test_tampered_ciphertext_raises_value_error(self, encryptor):
        ct = encryptor.encrypt("data")
        tampered = ct[:-4] + "XXXX"
        with pytest.raises(ValueError, match="Decryption failed"):
            encryptor.decrypt(tampered)

    def test_invalid_key_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid ENCRYPTION_KEY"):
            CredentialEncryptor("not-a-valid-fernet-key")

    def test_bytes_key_accepted(self):
        key_bytes = Fernet.generate_key()
        enc = CredentialEncryptor(key_bytes.decode())
        assert enc.decrypt(enc.encrypt("hello")) == "hello"


class TestGenerateFernetKey:
    def test_generates_valid_fernet_key(self):
        key = generate_fernet_key()
        # Should not raise
        Fernet(key.encode())

    def test_each_call_generates_unique_key(self):
        assert generate_fernet_key() != generate_fernet_key()

    def test_returns_string(self):
        assert isinstance(generate_fernet_key(), str)
