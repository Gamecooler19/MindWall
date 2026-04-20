"""IMAP proxy service — local IMAP server that intercepts mailbox access.

Phase 3 will implement:
  - Async IMAP protocol handling
  - Mindwall credential authentication
  - Upstream IMAP connection and UID mapping
  - Verdict-aware filtered mailbox views
  - Virtual quarantine folder
  - Latency-bounded verdict lookup (cached)
"""
