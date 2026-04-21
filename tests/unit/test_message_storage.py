"""Unit tests for app.messages.storage."""

import hashlib

import pytest
from app.messages.storage import RawMessageStore


@pytest.fixture()
def store(tmp_path):
    return RawMessageStore(tmp_path / "raw_messages")


class TestRawMessageStore:
    def test_write_returns_sha256_and_path(self, store):
        raw = b"From: test@example.com\r\n\r\nBody text."
        sha256, path = store.write(raw)
        assert len(sha256) == 64
        assert path.endswith(".eml")

    def test_sha256_matches_content(self, store):
        raw = b"Sample email content"
        sha256, _ = store.write(raw)
        expected = hashlib.sha256(raw).hexdigest()
        assert sha256 == expected

    def test_path_layout_uses_two_char_prefix(self, store):
        raw = b"Layout test content"
        sha256, path = store.write(raw)
        # Path should be {sha256[:2]}/{sha256}.eml
        assert path.startswith(sha256[:2] + "/") or path.startswith(sha256[:2] + "\\")

    def test_path_ends_with_sha256_and_eml(self, store):
        raw = b"Path format test"
        sha256, path = store.write(raw)
        assert path.endswith(f"{sha256}.eml")

    def test_file_is_written_to_disk(self, store, tmp_path):
        raw = b"Written to disk"
        _sha256, path = store.write(raw)
        full_path = tmp_path / "raw_messages" / path.replace("\\", "/")
        assert full_path.exists()
        assert full_path.read_bytes() == raw

    def test_write_is_idempotent(self, store):
        raw = b"Same content twice"
        sha256_a, path_a = store.write(raw)
        sha256_b, path_b = store.write(raw)
        assert sha256_a == sha256_b
        assert path_a == path_b

    def test_read_returns_original_bytes(self, store):
        raw = b"Read back this content"
        _, path = store.write(raw)
        result = store.read(path)
        assert result == raw

    def test_exists_false_before_write(self, store):
        fake_sha256 = "a" * 64
        assert store.exists(fake_sha256) is False

    def test_exists_true_after_write(self, store):
        raw = b"Existence check"
        sha256, _ = store.write(raw)
        assert store.exists(sha256) is True

    def test_directory_created_automatically(self, tmp_path):
        nested_path = tmp_path / "does" / "not" / "exist" / "yet"
        s = RawMessageStore(nested_path)
        raw = b"Auto-create directory"
        sha256, _path = s.write(raw)
        assert s.exists(sha256)

    def test_large_message_roundtrip(self, store):
        raw = b"X" * (1024 * 1024)  # 1 MB
        sha256, path = store.write(raw)
        result = store.read(path)
        assert result == raw
        assert store.exists(sha256)
