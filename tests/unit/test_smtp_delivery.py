"""Unit tests for app.proxies.smtp.delivery.

Covers:
  - _write_outbound_eml writes file and returns (sha256, rel_path).
  - _write_outbound_eml is idempotent (second call with same bytes skips write).
  - _extract_subject extracts Subject header.
  - _extract_subject returns None when no Subject header.
  - deliver_outbound capture mode creates OutboundMessage with CAPTURED status.
  - deliver_outbound capture mode persists correct fields.
  - deliver_outbound relay mode with missing profile marks FAILED.
"""

from __future__ import annotations

import asyncio
import hashlib
import textwrap

import pytest
from app.proxies.smtp.delivery import _extract_subject, _write_outbound_eml, deliver_outbound
from app.proxies.smtp.models import OutboundMessage, SmtpDeliveryStatus
from app.proxies.smtp.session import SmtpProxySession
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture()
def smtp_session():
    return SmtpProxySession(
        mailbox_profile_id=99,
        owner_user_id=1,
        proxy_username="mw_delivery_test",
        email_address="delivery@example.com",
    )


@pytest.fixture()
def sample_eml():
    return textwrap.dedent("""\
        From: sender@example.com
        To: rcpt@example.com
        Subject: Unit test message
        MIME-Version: 1.0
        Content-Type: text/plain

        Hello, this is a test body.
    """).encode()


# ---------------------------------------------------------------------------
# _write_outbound_eml
# ---------------------------------------------------------------------------


class TestWriteOutboundEml:
    def test_returns_sha256_and_path(self, tmp_path, sample_eml):
        sha256, rel_path = _write_outbound_eml(tmp_path, sample_eml)
        expected_sha256 = hashlib.sha256(sample_eml).hexdigest()
        assert sha256 == expected_sha256
        assert rel_path == f"{sha256[:2]}/{sha256}.eml"

    def test_file_is_written(self, tmp_path, sample_eml):
        _sha256, rel_path = _write_outbound_eml(tmp_path, sample_eml)
        full_path = tmp_path / rel_path
        assert full_path.exists()
        assert full_path.read_bytes() == sample_eml

    def test_idempotent(self, tmp_path, sample_eml):
        sha1, path1 = _write_outbound_eml(tmp_path, sample_eml)
        sha2, path2 = _write_outbound_eml(tmp_path, sample_eml)
        assert sha1 == sha2
        assert path1 == path2

    def test_subdirectory_created(self, tmp_path, sample_eml):
        sha256, _rel_path = _write_outbound_eml(tmp_path, sample_eml)
        subdir = tmp_path / sha256[:2]
        assert subdir.is_dir()


# ---------------------------------------------------------------------------
# _extract_subject
# ---------------------------------------------------------------------------


class TestExtractSubject:
    def test_extracts_subject(self):
        raw = b"Subject: Hello World\r\nFrom: a@b.com\r\n\r\nBody"
        assert _extract_subject(raw) == "Hello World"

    def test_returns_none_when_missing(self):
        raw = b"From: a@b.com\r\n\r\nBody"
        assert _extract_subject(raw) is None

    def test_returns_none_on_invalid_bytes(self):
        result = _extract_subject(b"")
        assert result is None

    def test_subject_truncated_to_998(self):
        long_subj = "X" * 1500
        raw = f"Subject: {long_subj}\r\n\r\n".encode()
        result = _extract_subject(raw)
        assert result is not None
        assert len(result) <= 998


# ---------------------------------------------------------------------------
# deliver_outbound — capture mode
# ---------------------------------------------------------------------------


class TestDeliverOutboundCapture:
    def test_capture_creates_outbound_message(self, factory, smtp_session, sample_eml, tmp_path):
        async def _run():
            async with factory() as db:
                msg = await deliver_outbound(
                    db=db,
                    session=smtp_session,
                    envelope_from="sender@example.com",
                    envelope_to=["rcpt@example.com"],
                    raw_message=sample_eml,
                    store_root=tmp_path,
                    delivery_mode="capture",
                )
                return msg

        msg = asyncio.get_event_loop().run_until_complete(_run())
        assert msg is not None
        assert isinstance(msg, OutboundMessage)

    def test_capture_status_is_captured(self, factory, smtp_session, sample_eml, tmp_path):
        async def _run():
            async with factory() as db:
                return await deliver_outbound(
                    db=db,
                    session=smtp_session,
                    envelope_from="sender@example.com",
                    envelope_to=["rcpt@example.com"],
                    raw_message=sample_eml,
                    store_root=tmp_path,
                    delivery_mode="capture",
                )

        msg = asyncio.get_event_loop().run_until_complete(_run())
        assert msg.delivery_status == SmtpDeliveryStatus.CAPTURED

    def test_capture_persists_envelope_fields(self, factory, smtp_session, sample_eml, tmp_path):
        async def _run():
            async with factory() as db:
                return await deliver_outbound(
                    db=db,
                    session=smtp_session,
                    envelope_from="from@example.com",
                    envelope_to=["to1@example.com", "to2@example.com"],
                    raw_message=sample_eml,
                    store_root=tmp_path,
                    delivery_mode="capture",
                )

        msg = asyncio.get_event_loop().run_until_complete(_run())
        assert msg.envelope_from == "from@example.com"
        assert "to1@example.com" in msg.envelope_to_json
        assert "to2@example.com" in msg.envelope_to_json
        assert msg.subject == "Unit test message"
        assert msg.proxy_username == "mw_delivery_test"
        assert msg.mailbox_profile_id == 99
        assert msg.raw_size_bytes == len(sample_eml)

    def test_capture_sha256_matches(self, factory, smtp_session, sample_eml, tmp_path):
        async def _run():
            async with factory() as db:
                return await deliver_outbound(
                    db=db,
                    session=smtp_session,
                    envelope_from="a@example.com",
                    envelope_to=["b@example.com"],
                    raw_message=sample_eml,
                    store_root=tmp_path,
                    delivery_mode="capture",
                )

        msg = asyncio.get_event_loop().run_until_complete(_run())
        expected = hashlib.sha256(sample_eml).hexdigest()
        assert msg.raw_sha256 == expected

    def test_capture_writes_eml_to_disk(self, factory, smtp_session, sample_eml, tmp_path):
        async def _run():
            async with factory() as db:
                return await deliver_outbound(
                    db=db,
                    session=smtp_session,
                    envelope_from="a@example.com",
                    envelope_to=["b@example.com"],
                    raw_message=sample_eml,
                    store_root=tmp_path,
                    delivery_mode="capture",
                )

        msg = asyncio.get_event_loop().run_until_complete(_run())
        assert msg.raw_storage_path is not None
        full_path = tmp_path / msg.raw_storage_path
        assert full_path.exists()
        assert full_path.read_bytes() == sample_eml


# ---------------------------------------------------------------------------
# deliver_outbound — relay mode without profile (misconfigured)
# ---------------------------------------------------------------------------


class TestDeliverOutboundRelayMisconfigured:
    def test_relay_without_profile_marks_failed(self, factory, smtp_session, sample_eml, tmp_path):
        async def _run():
            async with factory() as db:
                return await deliver_outbound(
                    db=db,
                    session=smtp_session,
                    envelope_from="a@example.com",
                    envelope_to=["b@example.com"],
                    raw_message=sample_eml,
                    store_root=tmp_path,
                    delivery_mode="relay",
                    # No encryptor or profile — should fail gracefully.
                )

        msg = asyncio.get_event_loop().run_until_complete(_run())
        assert msg.delivery_status == SmtpDeliveryStatus.FAILED
        assert msg.relay_error is not None
        assert len(msg.relay_error) > 0
