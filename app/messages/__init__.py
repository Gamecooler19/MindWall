"""Messages domain — RFC 5322 parsing, MIME normalisation, and artifact extraction.

Phase 3 will implement:
  - Raw email parsing (Python stdlib email module)
  - Multipart body decoding
  - HTML → safe-text extraction
  - URL and domain extraction
  - Attachment metadata and hashing
  - Header normalisation (SPF/DKIM/DMARC results, display-name, reply-to)
  - Canonical MessageRecord for downstream analysis
"""
