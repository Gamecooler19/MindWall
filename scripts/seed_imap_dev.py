"""Dev-only IMAP proxy seed script.

Creates a mailbox profile with known proxy credentials, injects two synthetic
messages directly into the local message store, and creates MailboxItem rows
that the IMAP proxy will serve.  This is intentionally a dev/QA tool — never
run it in production.

Seed data created:
  - 1 MailboxProfile owned by the admin user (id=1)
      proxy_username:  mw_seed_imap_dev
      proxy_password:  seed-imap-dev-password-2024
  - 2 Message rows with synthetic .eml content
  - 3 MailboxItem rows:
      INBOX/1 — VISIBLE  (message 1: phishing test email from attacker)
      INBOX/2 — VISIBLE  (message 2: normal newsletter)
      INBOX/3 — QUARANTINED  (message 3: quarantine test)

The script is idempotent: if the mailbox profile already exists (identified by
proxy_username) it reuses it and updates proxy_password_hash.

Usage:
    python scripts/seed_imap_dev.py
    # or in Docker:
    docker exec mindwall_app python scripts/seed_imap_dev.py
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import bcrypt
from app.mailboxes.models import ImapSecurity, MailboxProfile, MailboxStatus, SmtpSecurity
from app.mailboxes.sync_models import ItemVisibility, MailboxItem
from app.messages.models import IngestionSource, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROXY_USERNAME = "mw_seed_imap_dev"
PROXY_PASSWORD = "seed-imap-dev-password-2024"

# Three synthetic messages: 2 VISIBLE, 1 QUARANTINED
_MESSAGES = [
    {
        "message_id": "<msg-seed-001@mindwall.local>",
        "subject": "[SEED] Phishing test — Mindwall IMAP dev seed",
        "from_address": "attacker@evil.example",
        "from_display_name": "PayPal Security Team",
        "date": "Mon, 01 Jan 2024 09:00:00 +0000",
        "body_text": (
            "URGENT: Your account has been compromised.\n"
            "Click here to verify: http://evil.example/steal-credentials\n"
            "Failure to act within 24 hours will result in suspension.\n"
        ),
        "visibility": ItemVisibility.VISIBLE,
        "upstream_uid": 1001,
    },
    {
        "message_id": "<msg-seed-002@mindwall.local>",
        "subject": "[SEED] Your weekly newsletter",
        "from_address": "newsletter@example.com",
        "from_display_name": "Example Newsletter",
        "date": "Tue, 02 Jan 2024 10:00:00 +0000",
        "body_text": (
            "Hello subscriber,\n"
            "Here is your weekly roundup of news and updates.\n"
            "Visit our site: https://example.com/newsletter\n"
        ),
        "visibility": ItemVisibility.VISIBLE,
        "upstream_uid": 1002,
    },
    {
        "message_id": "<msg-seed-003@mindwall.local>",
        "subject": "[SEED] Quarantined — credential harvesting attempt",
        "from_address": "fraud@phishing.example",
        "from_display_name": "Your Bank",
        "date": "Wed, 03 Jan 2024 11:00:00 +0000",
        "body_text": (
            "Dear valued customer,\n"
            "Please update your credit card details immediately:\n"
            "https://phishing.example/update-card\n"
            "Your account will be locked if you do not comply.\n"
        ),
        "visibility": ItemVisibility.QUARANTINED,
        "upstream_uid": 1003,
    },
]


def _build_eml(msg: dict) -> bytes:
    """Synthesise a minimal valid RFC 5322 .eml from seed data."""
    lines = [
        f"From: {msg['from_display_name']} <{msg['from_address']}>",
        "To: seed-user@mindwall.local",
        f"Subject: {msg['subject']}",
        f"Date: {msg['date']}",
        f"Message-ID: {msg['message_id']}",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
        "",
        msg["body_text"],
    ]
    return "\r\n".join(lines).encode("utf-8")


def _write_eml(root: Path, raw_bytes: bytes) -> tuple[str, str]:
    """Write an .eml to the raw message store and return (sha256, path)."""
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    relative_path = f"{sha256[:2]}/{sha256}.eml"
    full_path = root / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    if not full_path.exists():
        tmp = full_path.with_suffix(".tmp")
        tmp.write_bytes(raw_bytes)
        tmp.replace(full_path)
        print(f"  Wrote .eml: {relative_path}")
    else:
        print(f"  .eml already exists: {relative_path}")
    return sha256, relative_path


async def run(db: AsyncSession, raw_store_root: Path) -> None:
    """Main seed logic — idempotent, safe to run multiple times."""

    # -----------------------------------------------------------------
    # 1. Ensure the admin user (id=1) exists
    # -----------------------------------------------------------------
    from app.users.models import User

    result = await db.execute(select(User).where(User.id == 1))
    admin = result.scalar_one_or_none()
    if admin is None:
        print("ERROR: Admin user (id=1) not found. Run migrations + create_admin first.")
        sys.exit(1)
    print(f"Admin user: {admin.email} (id={admin.id})")

    # -----------------------------------------------------------------
    # 2. Upsert the seed MailboxProfile
    # -----------------------------------------------------------------
    result = await db.execute(
        select(MailboxProfile).where(MailboxProfile.proxy_username == PROXY_USERNAME)
    )
    profile = result.scalar_one_or_none()

    proxy_hash = bcrypt.hashpw(PROXY_PASSWORD.encode(), bcrypt.gensalt()).decode()

    if profile is None:
        profile = MailboxProfile(
            owner_id=admin.id,
            email_address="seed-user@mindwall.local",
            display_name="IMAP Dev Seed User",
            imap_host="imap.example.com",
            imap_port=993,
            imap_username="seed-user@mindwall.local",
            imap_password_enc="seed-placeholder-not-real",
            imap_security=ImapSecurity.SSL_TLS,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_username="seed-user@mindwall.local",
            smtp_password_enc="seed-placeholder-not-real",
            smtp_security=SmtpSecurity.STARTTLS,
            proxy_username=PROXY_USERNAME,
            proxy_password_hash=proxy_hash,
            status=MailboxStatus.ACTIVE,
        )
        db.add(profile)
        await db.flush()
        print(f"Created MailboxProfile id={profile.id}")
    else:
        profile.proxy_password_hash = proxy_hash
        profile.status = MailboxStatus.ACTIVE
        await db.flush()
        print(f"Reused MailboxProfile id={profile.id}")

    # -----------------------------------------------------------------
    # 3. Seed messages + mailbox items
    # -----------------------------------------------------------------
    for seed in _MESSAGES:
        # Check if MailboxItem already exists for this uid
        result = await db.execute(
            select(MailboxItem).where(
                MailboxItem.mailbox_profile_id == profile.id,
                MailboxItem.folder_name == "INBOX",
                MailboxItem.upstream_uid == seed["upstream_uid"],
            )
        )
        item = result.scalar_one_or_none()
        if item is not None:
            print(f"  MailboxItem uid={seed['upstream_uid']} already exists, skipping")
            continue

        # Write .eml to store
        raw_bytes = _build_eml(seed)
        sha256, rel_path = _write_eml(raw_store_root, raw_bytes)

        # Create Message record
        message = Message(
            mailbox_profile_id=profile.id,
            message_id=seed["message_id"],
            subject=seed["subject"],
            from_address=seed["from_address"],
            from_display_name=seed["from_display_name"],
            date=datetime.strptime(seed["date"], "%a, %d %b %Y %H:%M:%S %z"),
            has_text_plain=True,
            has_text_html=False,
            text_plain=seed["body_text"],
            raw_size_bytes=len(raw_bytes),
            raw_sha256=sha256,
            raw_storage_path=rel_path,
            header_dkim_signature_present=False,
            num_attachments=0,
            num_urls=1,
            ingestion_source=IngestionSource.IMAP_SYNC,
        )
        db.add(message)
        await db.flush()

        # Create MailboxItem
        mail_item = MailboxItem(
            mailbox_profile_id=profile.id,
            folder_name="INBOX",
            upstream_uid=seed["upstream_uid"],
            uid_validity=20240101,
            message_id=message.id,
            rfc_message_id=seed["message_id"],
            visibility=seed["visibility"],
            mindwall_uid=seed["upstream_uid"],  # use upstream_uid as mindwall_uid for simplicity
        )
        db.add(mail_item)
        await db.flush()
        print(
            f"  Created Message id={message.id} + "
            f"MailboxItem uid={seed['upstream_uid']} visibility={seed['visibility']}"
        )

    await db.commit()
    print()
    print("=" * 60)
    print("Seed complete.")
    print(f"  proxy_username: {PROXY_USERNAME}")
    print(f"  proxy_password: {PROXY_PASSWORD}")
    print(f"  mailbox_profile_id: {profile.id}")
    print()
    print("INBOX (VISIBLE):")
    print("  UID 1001 — [SEED] Phishing test")
    print("  UID 1002 — [SEED] Your weekly newsletter")
    print()
    print("Mindwall/Quarantine (QUARANTINED):")
    print("  UID 1003 — [SEED] Quarantined — credential harvesting attempt")
    print("=" * 60)


async def main() -> None:
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://mindwall:mindwall@localhost:5432/mindwall",
    )
    raw_store_root = Path(
        os.environ.get("RAW_MESSAGE_STORE_PATH", "./data/raw_messages")
    )

    print(f"Connecting to: {database_url}")
    print(f"Raw message store: {raw_store_root}")

    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as db:
        await run(db, raw_store_root)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
