"""Message ingestion service.

Orchestrates the full ingestion pipeline: parse → store → persist.

This service is intentionally thin: it delegates to the parser, storage, and
ORM models.  Route handlers and future proxy/gateway code should call this
service rather than the lower-level modules directly.

Entry points:
  ingest_raw_message   — parse, store, and persist a raw .eml
  get_message_by_id    — load a single message with its URLs + attachments
  list_messages        — paginated list of ingested messages
"""

import json

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.messages.models import IngestionSource, Message, MessageAttachment, MessageUrl
from app.messages.parser import parse_message
from app.messages.storage import RawMessageStore

log = structlog.get_logger(__name__)


async def ingest_raw_message(
    db: AsyncSession,
    raw_bytes: bytes,
    source: IngestionSource,
    store: RawMessageStore,
    mailbox_profile_id: int | None = None,
) -> Message:
    """Parse, store, and persist a raw RFC 5322 message.

    Args:
        db:                   Active async database session.
        raw_bytes:            The raw .eml content.
        source:               Ingestion entry point (lab, proxy, gateway).
        store:                Raw message file store instance.
        mailbox_profile_id:   Optional owning mailbox profile (None for lab).

    Returns:
        The committed Message ORM record with id populated.
    """
    parsed = parse_message(raw_bytes)

    # Write to the filesystem store (idempotent by SHA-256).
    sha256, storage_path = store.write(raw_bytes)

    log.info(
        "messages.ingesting",
        source=source.value,
        sha256=sha256,
        size_bytes=parsed.raw_size_bytes,
        subject=parsed.subject,
        num_urls=len(parsed.urls),
        num_attachments=len(parsed.attachments),
    )

    message = Message(
        mailbox_profile_id=mailbox_profile_id,
        message_id=parsed.message_id,
        in_reply_to=parsed.in_reply_to,
        references=parsed.references,
        subject=parsed.subject,
        from_address=parsed.from_address,
        from_display_name=parsed.from_display_name,
        reply_to_address=parsed.reply_to_address,
        to_addresses=json.dumps(parsed.to_addresses) if parsed.to_addresses else None,
        cc_addresses=json.dumps(parsed.cc_addresses) if parsed.cc_addresses else None,
        bcc_addresses=json.dumps(parsed.bcc_addresses) if parsed.bcc_addresses else None,
        date=parsed.date,
        has_text_plain=parsed.has_text_plain,
        has_text_html=parsed.has_text_html,
        text_plain=parsed.text_plain,
        text_html_safe=parsed.text_html_safe,
        raw_size_bytes=parsed.raw_size_bytes,
        raw_sha256=sha256,
        raw_storage_path=storage_path,
        header_authentication_results=parsed.header_authentication_results,
        header_received_spf=parsed.header_received_spf,
        header_dkim_signature_present=parsed.header_dkim_signature_present,
        header_x_mailer=parsed.header_x_mailer,
        num_attachments=len(parsed.attachments),
        num_urls=len(parsed.urls),
        ingestion_source=source,
    )
    db.add(message)
    await db.flush()  # populate message.id without committing

    for i, url in enumerate(parsed.urls):
        db.add(
            MessageUrl(
                message_id=message.id,
                raw_url=url.raw_url,
                normalized_url=url.normalized_url,
                scheme=url.scheme,
                host=url.host,
                path=url.path,
                source=url.source,
                link_text=url.link_text,
                position=i,
            )
        )

    for i, att in enumerate(parsed.attachments):
        db.add(
            MessageAttachment(
                message_id=message.id,
                filename=att.filename,
                content_type=att.content_type,
                size_bytes=att.size_bytes,
                sha256=att.sha256,
                is_inline=att.is_inline,
                content_id=att.content_id,
                position=i,
            )
        )

    await db.commit()
    await db.refresh(message)

    log.info(
        "messages.ingested",
        db_message_id=message.id,
        sha256=sha256,
    )

    return message


async def get_message_by_id(db: AsyncSession, message_id: int) -> Message | None:
    """Load a single message by its database ID, eagerly loading URLs and attachments."""
    result = await db.execute(
        select(Message)
        .where(Message.id == message_id)
        .options(
            selectinload(Message.urls),
            selectinload(Message.attachments),
        )
    )
    return result.scalar_one_or_none()


async def list_messages(
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> list[Message]:
    """Return the most recently ingested messages (newest first)."""
    result = await db.execute(
        select(Message).order_by(Message.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())
