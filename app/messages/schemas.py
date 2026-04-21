"""Data-transfer objects for the messages domain.

These dataclasses represent the parsed/normalized message as it travels
through the ingestion pipeline. They are distinct from the ORM models
so that parsing logic has no database dependency.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ExtractedUrl:
    """A URL found in a message body."""

    raw_url: str
    normalized_url: str
    scheme: str
    host: str
    path: str
    source: str  # 'text' | 'html'
    link_text: str | None = None


@dataclass
class ExtractedAttachment:
    """Metadata for a single MIME attachment part."""

    content_type: str
    size_bytes: int
    is_inline: bool
    filename: str | None = None
    sha256: str | None = None
    content_id: str | None = None


@dataclass
class ParsedMessage:
    """Canonical normalized message representation produced by the parser.

    Downstream consumers (service, analysis, policy) work from this object
    rather than directly parsing the raw RFC 5322 bytes.
    """

    # Raw bytes metadata
    raw_size_bytes: int
    raw_sha256: str

    # RFC 5322 identifiers
    message_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None

    # Envelope
    subject: str | None = None
    from_address: str | None = None
    from_display_name: str | None = None
    reply_to_address: str | None = None
    to_addresses: list[str] = field(default_factory=list)
    cc_addresses: list[str] = field(default_factory=list)
    bcc_addresses: list[str] = field(default_factory=list)
    date: datetime | None = None

    # Body
    has_text_plain: bool = False
    has_text_html: bool = False
    text_plain: str | None = None
    text_html_safe: str | None = None

    # Storage reference — set by the service layer after write
    raw_storage_path: str | None = None

    # Authentication headers
    header_authentication_results: str | None = None
    header_received_spf: str | None = None
    header_dkim_signature_present: bool = False
    header_x_mailer: str | None = None

    # Extracted artifacts
    urls: list[ExtractedUrl] = field(default_factory=list)
    attachments: list[ExtractedAttachment] = field(default_factory=list)
