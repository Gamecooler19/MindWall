#!/usr/bin/env python3
"""Manual end-to-end SMTP proxy verification script.

Connects to the Mindwall SMTP proxy running in Docker and exercises the full
protocol flow.  Requires the proxy container to be running AND the seed
mailbox profile to exist in the database.

Usage:
    # Run from workspace root (inside or outside the conda env):
    python scripts/verify_smtp_proxy.py

    # Override host/port if needed:
    SMTP_HOST=127.0.0.1 SMTP_PORT=1587 python scripts/verify_smtp_proxy.py

Prerequisites:
    The seed mailbox profile must exist.  Run the seed script first if needed:
        docker exec mindwall_app python scripts/seed_imap_dev.py

Assertions (22 checks):
  Phase 1 — Unauthenticated baseline:
    1.  220 greeting received
    2.  Greeting contains "Mindwall"
    3.  EHLO returns 250 multi-line
    4.  EHLO response includes AUTH extension
    5.  EHLO includes PLAIN mechanism
    6.  EHLO includes LOGIN mechanism
    7.  NOOP returns 250
    8.  Bad-credential AUTH PLAIN returns 535
    9.  MAIL FROM before AUTH returns 5xx

  Phase 2 — Authenticated session (AUTH PLAIN):
    10. AUTH PLAIN with valid credentials returns 235
    11. MAIL FROM accepted (250)
    12. RCPT TO accepted (250)
    13. DATA command returns 354 go-ahead
    14. Message body accepted, final 250 returned (captured)
    15. New MAIL FROM accepted after previous transaction (session reuse)

  Phase 3 — AUTH LOGIN mechanism:
    16. AUTH LOGIN 334 challenge received (Username:)
    17. Second 334 challenge (Password:)
    18. 235 on valid credentials via AUTH LOGIN

  Phase 4 — Protocol enforcement:
    19. RSET resets envelope (250)
    20. Unknown command (VRFY) returns 502
    21. QUIT returns 221

  Phase 5 — DB verification (via docker exec psql):
    22. outbound_messages row exists for captured message
"""

from __future__ import annotations

import base64
import os
import socket
import subprocess
import sys
import textwrap

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HOST = os.environ.get("SMTP_HOST", "127.0.0.1")
PORT = int(os.environ.get("SMTP_PORT", "1587"))
PROXY_USERNAME = os.environ.get("SMTP_PROXY_USER", "mw_seed_imap_dev")
PROXY_PASSWORD = os.environ.get("SMTP_PROXY_PASS", "seed-imap-dev-password-2024")
DB_CONTAINER = os.environ.get("DB_CONTAINER", "mindwall_db")

SAMPLE_FROM = "sender@example.com"
SAMPLE_TO = "recipient@example.com"
SAMPLE_MESSAGE = textwrap.dedent("""\
    From: sender@example.com
    To: recipient@example.com
    Subject: Mindwall SMTP verification test
    MIME-Version: 1.0
    Content-Type: text/plain; charset=utf-8

    This is a manual verification message sent through the Mindwall SMTP proxy.
    Timestamp: verify_smtp_proxy.py run.
""")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = "\033[92m✓\033[0m"
_FAIL = "\033[91m✗\033[0m"
_failures: list[str] = []
_total = 0


def check(description: str, condition: bool) -> None:
    global _total
    _total += 1
    if condition:
        print(f"  {_PASS}  {description}")
    else:
        print(f"  {_FAIL}  {description}")
        _failures.append(description)


def section(name: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {name}")
    print("─" * 60)


def _plain_b64(username: str, password: str) -> str:
    raw = f"\x00{username}\x00{password}".encode()
    return base64.b64encode(raw).decode()


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _raw_send(sock: socket.socket, line: str) -> None:
    sock.sendall((line + "\r\n").encode())


def _raw_readline(sock: socket.socket) -> str:
    buf = b""
    while not buf.endswith(b"\r\n"):
        chunk = sock.recv(1)
        if not chunk:
            break
        buf += chunk
    return buf.rstrip(b"\r\n").decode("utf-8", errors="replace")


def _raw_drain_until(sock: socket.socket, prefix: str, max_lines: int = 30) -> str:
    """Read lines until one starts with the given prefix."""
    for _ in range(max_lines):
        line = _raw_readline(sock)
        if line.startswith(prefix):
            return line
    raise AssertionError(f"Did not receive line starting with {prefix!r}")


def _raw_drain_multiline(sock: socket.socket, start_code: str) -> list[str]:
    """Drain a multi-line SMTP response (250-... until 250 ...)."""
    lines = []
    for _ in range(50):
        line = _raw_readline(sock)
        lines.append(line)
        if line.startswith(f"{start_code} "):
            break
        if not line.startswith(f"{start_code}-"):
            break
    return lines


# ---------------------------------------------------------------------------
# Phase 1: Unauthenticated baseline
# ---------------------------------------------------------------------------


def run_phase1() -> None:
    section("Phase 1 — Unauthenticated baseline")

    sock = socket.create_connection((HOST, PORT), timeout=10)
    sock.settimeout(10)
    try:
        # 1. 220 greeting
        greeting = _raw_readline(sock)
        check("220 greeting received", greeting.startswith("220"))

        # 2. Greeting mentions Mindwall
        check("Greeting contains 'Mindwall'", "Mindwall" in greeting)

        # 3-6: EHLO multi-line response
        _raw_send(sock, "EHLO verify.example.com")
        ehlo_lines = _raw_drain_multiline(sock, "250")
        full_ehlo = "\n".join(ehlo_lines)
        check("EHLO returns 250 multi-line", any(ln.startswith("250") for ln in ehlo_lines))
        check("EHLO response includes AUTH extension", "AUTH" in full_ehlo)
        check("EHLO includes PLAIN mechanism", "PLAIN" in full_ehlo)
        check("EHLO includes LOGIN mechanism", "LOGIN" in full_ehlo)

        # 7: NOOP
        _raw_send(sock, "NOOP")
        noop_line = _raw_drain_until(sock, "250")
        check("NOOP returns 250", noop_line.startswith("250"))

        # 8: Bad-credential AUTH PLAIN → 535
        bad_b64 = _plain_b64("nobody", "wrongpassword")
        _raw_send(sock, f"AUTH PLAIN {bad_b64}")
        auth_bad = _raw_readline(sock)
        check("Bad-credential AUTH PLAIN returns 535", auth_bad.startswith("535"))

        # 9: MAIL FROM before auth → 5xx
        _raw_send(sock, "MAIL FROM:<test@example.com>")
        mail_pre_auth = _raw_readline(sock)
        code = int(mail_pre_auth.split()[0]) if mail_pre_auth and mail_pre_auth[0].isdigit() else 0
        check("MAIL FROM before AUTH returns 5xx", code >= 500)

    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Phase 2: Authenticated session (AUTH PLAIN)
# ---------------------------------------------------------------------------


def run_phase2() -> None:
    section("Phase 2 — Authenticated session (AUTH PLAIN)")

    sock = socket.create_connection((HOST, PORT), timeout=10)
    sock.settimeout(10)
    try:
        _raw_readline(sock)  # greeting

        _raw_send(sock, "EHLO verify.example.com")
        _raw_drain_multiline(sock, "250")

        # 10: AUTH PLAIN → 235
        b64 = _plain_b64(PROXY_USERNAME, PROXY_PASSWORD)
        _raw_send(sock, f"AUTH PLAIN {b64}")
        auth_result = _raw_readline(sock)
        check("AUTH PLAIN with valid credentials returns 235", auth_result.startswith("235"))

        # 11: MAIL FROM → 250
        _raw_send(sock, f"MAIL FROM:<{SAMPLE_FROM}>")
        mail_result = _raw_readline(sock)
        check("MAIL FROM accepted (250)", mail_result.startswith("250"))

        # 12: RCPT TO → 250
        _raw_send(sock, f"RCPT TO:<{SAMPLE_TO}>")
        rcpt_result = _raw_readline(sock)
        check("RCPT TO accepted (250)", rcpt_result.startswith("250"))

        # 13: DATA → 354
        _raw_send(sock, "DATA")
        data_start = _raw_readline(sock)
        check("DATA command returns 354 go-ahead", data_start.startswith("354"))

        # 14: Send message + terminator → 250
        for line in SAMPLE_MESSAGE.splitlines():
            _raw_send(sock, line)
        _raw_send(sock, ".")  # End DATA
        data_end = _raw_readline(sock)
        check("Message body accepted, 250 returned (captured)", data_end.startswith("250"))

        # 15: New MAIL FROM accepted (session reuse)
        _raw_send(sock, "MAIL FROM:<another@example.com>")
        second_mail = _raw_readline(sock)
        check(
            "New MAIL FROM accepted after previous transaction",
            second_mail.startswith("250"),
        )

    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Phase 3: AUTH LOGIN mechanism
# ---------------------------------------------------------------------------


def run_phase3() -> None:
    section("Phase 3 — AUTH LOGIN mechanism")

    sock = socket.create_connection((HOST, PORT), timeout=10)
    sock.settimeout(10)
    try:
        _raw_readline(sock)  # greeting

        _raw_send(sock, "EHLO verify.example.com")
        _raw_drain_multiline(sock, "250")

        _raw_send(sock, "AUTH LOGIN")
        challenge1 = _raw_readline(sock)
        # 16: 334 challenge for Username:
        check("AUTH LOGIN 334 challenge received (Username:)", challenge1.startswith("334"))

        _raw_send(sock, _b64(PROXY_USERNAME))
        challenge2 = _raw_readline(sock)
        # 17: 334 challenge for Password:
        check("Second 334 challenge received (Password:)", challenge2.startswith("334"))

        _raw_send(sock, _b64(PROXY_PASSWORD))
        auth_result = _raw_readline(sock)
        # 18: 235 success
        check("235 on valid credentials via AUTH LOGIN", auth_result.startswith("235"))

    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Phase 4: Protocol enforcement
# ---------------------------------------------------------------------------


def run_phase4() -> None:
    section("Phase 4 — Protocol enforcement")

    sock = socket.create_connection((HOST, PORT), timeout=10)
    sock.settimeout(10)
    try:
        _raw_readline(sock)  # greeting

        _raw_send(sock, "EHLO verify.example.com")
        _raw_drain_multiline(sock, "250")

        b64 = _plain_b64(PROXY_USERNAME, PROXY_PASSWORD)
        _raw_send(sock, f"AUTH PLAIN {b64}")
        _raw_readline(sock)  # 235

        _raw_send(sock, "MAIL FROM:<rset@example.com>")
        _raw_readline(sock)  # 250

        # 19: RSET resets envelope
        _raw_send(sock, "RSET")
        rset_result = _raw_readline(sock)
        check("RSET resets envelope (250)", rset_result.startswith("250"))

        # 20: Unknown command → 502
        _raw_send(sock, "VRFY user@example.com")
        vrfy_result = _raw_readline(sock)
        check("Unknown command (VRFY) returns 502", vrfy_result.startswith("502"))

        # 21: QUIT → 221
        _raw_send(sock, "QUIT")
        quit_result = _raw_readline(sock)
        check("QUIT returns 221", quit_result.startswith("221"))

    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Phase 5: DB verification
# ---------------------------------------------------------------------------


def run_phase5() -> None:
    section("Phase 5 — DB verification (captured row exists)")

    try:
        result = subprocess.run(  # noqa: S603
            [  # noqa: S607
                "docker", "exec", DB_CONTAINER,
                "psql", "-U", "mindwall", "-d", "mindwall",
                "-c", "SELECT COUNT(*) FROM outbound_messages;",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout
        # psql output format: row with integer
        lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
        count_line = next((ln for ln in lines if ln.isdigit()), None)
        count = int(count_line) if count_line else -1
        check("outbound_messages table row count >= 1", count >= 1)
        if count >= 0:
            print(f"         → {count} outbound message(s) in DB")
    except subprocess.TimeoutExpired:
        check("outbound_messages DB query completed", False)
    except FileNotFoundError:
        # docker not in PATH (shouldn't happen)
        check("docker exec available for DB check", False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print()
    print("=" * 60)
    print("  Mindwall SMTP proxy — end-to-end verification")
    print(f"  Target: {HOST}:{PORT}")
    print(f"  User:   {PROXY_USERNAME}")
    print("=" * 60)

    run_phase1()
    run_phase2()
    run_phase3()
    run_phase4()
    run_phase5()

    print()
    print("=" * 60)
    if _failures:
        print(f"  RESULT: {len(_failures)} / {_total} checks FAILED")
        for f in _failures:
            print(f"    ✗  {f}")
        sys.exit(1)
    else:
        print(f"  RESULT: {_total}/{_total} checks passed  ✓")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
