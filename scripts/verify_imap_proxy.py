"""Manual IMAP verification script.

Connects to the Mindwall IMAP proxy at localhost:1143 and exercises
the basic IMAP command set.

Usage:
    python scripts/verify_imap_proxy.py

Prerequisites:
    - Docker stack running: docker compose --env-file .env.docker up -d
    - IMAP proxy listening on localhost:1143
"""

import imaplib
import sys


def main() -> int:
    host = "localhost"
    port = 1143

    print(f"Connecting to IMAP proxy at {host}:{port}...")
    try:
        imap = imaplib.IMAP4(host, port)
    except OSError as e:
        print(f"FAIL: Could not connect: {e}")
        return 1

    print(f"Connected. Greeting: {imap.welcome!r}")

    # CAPABILITY
    typ, caps = imap.capability()
    print(f"CAPABILITY ({typ}): {caps}")
    assert b"IMAP4rev1" in caps[0], "CAPABILITY should include IMAP4rev1"

    # NOOP
    typ, _ = imap.noop()
    print(f"NOOP: {typ}")
    assert typ == "OK"

    # LOGIN with bad credentials — should raise an error
    try:
        imap.login("nobody_doesnotexist", "wrong_password")
        print("FAIL: Bad login should have been rejected")
        return 1
    except imaplib.IMAP4.error as e:
        print(f"Bad login correctly rejected: {str(e)[:80]}")

    # LOGOUT
    imap.logout()
    print("LOGOUT: OK")

    print()
    print("=" * 50)
    print("Manual IMAP proxy verification PASSED")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
