"""Manual IMAP proxy end-to-end verification script.

Performs a full authenticated IMAP session against the Mindwall IMAP proxy,
verifying every stage of the read-only proxy flow:

  Phase A — Unauthenticated baseline
    1. Connect and check greeting
    2. CAPABILITY includes IMAP4rev1
    3. NOOP in NOT_AUTH state
    4. Bad-credential LOGIN rejected
    5. LIST before login rejected with NO

  Phase B — Authenticated session with real DB-backed data
    6. LOGIN with real proxy credentials
    7. LIST returns INBOX and Mindwall/Quarantine
    8. SELECT INBOX — EXISTS >= 1
    9. UID SEARCH ALL in INBOX — returns UID(s)
   10. UID FETCH first UID (UID FLAGS RFC822.SIZE ENVELOPE BODY[])
   11. SELECT Mindwall/Quarantine — EXISTS >= 1
   12. UID SEARCH ALL in quarantine — returns UID(s)
   13. UID FETCH quarantine message (UID FLAGS BODY[HEADER])

  Phase C — Read-only mutation rejection
   14. STORE — rejected with NO [CANNOT]
   15. EXPUNGE — rejected with NO [CANNOT]
   16. COPY — rejected with NO [CANNOT]
   17. APPEND — rejected with NO [CANNOT]

Prerequisites:
    - Docker stack running: docker compose --env-file .env.docker up -d
    - Seed data: docker exec mindwall_app python scripts/seed_imap_dev.py
    - IMAP proxy listening on localhost:1143

Usage:
    python scripts/verify_imap_proxy.py
"""

from __future__ import annotations

import imaplib
import sys

# ---------------------------------------------------------------------------
# Seed credentials created by scripts/seed_imap_dev.py
# ---------------------------------------------------------------------------
PROXY_USERNAME = "mw_seed_imap_dev"
PROXY_PASSWORD = "seed-imap-dev-password-2024"
QUARANTINE_FOLDER = "Mindwall/Quarantine"

_PASS = "\033[32mPASS\033[0m"
_FAIL = "\033[31mFAIL\033[0m"

_failures: list[str] = []


def _ok(label: str) -> None:
    print(f"  [{_PASS}] {label}")


def _fail(label: str, detail: str = "") -> None:
    msg = f"{label}: {detail}" if detail else label
    print(f"  [{_FAIL}] {msg}")
    _failures.append(msg)


def _assert(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        _ok(label)
    else:
        _fail(label, detail)


def _raw(imap: imaplib.IMAP4, cmd: str) -> tuple[str, list[bytes]]:
    """Send a raw tagged command and return (typ, data)."""
    typ, _data = imap._simple_command(cmd)
    rsp = imap._get_response()
    return typ, [rsp] if rsp else []


def _send_raw(imap: imaplib.IMAP4, tag: str, line: str) -> str:
    """Send a fully formed tagged IMAP command and return raw response lines."""
    imap.send(f"{tag} {line}\r\n".encode())
    lines: list[str] = []
    while True:
        raw = imap.readline()
        decoded = raw.decode("utf-8", errors="replace").rstrip()
        lines.append(decoded)
        if decoded.startswith(f"{tag} "):
            break
    return "\n".join(lines)


def main() -> int:
    host = "localhost"
    port = 1143

    # ------------------------------------------------------------------
    print("\n" + "=" * 62)
    print(" Phase A — Unauthenticated baseline")
    print("=" * 62)
    # ------------------------------------------------------------------

    print(f"\nConnecting to IMAP proxy at {host}:{port}...")
    try:
        imap = imaplib.IMAP4(host, port)
    except OSError as e:
        print(f"[{_FAIL}] Could not connect: {e}")
        return 1

    greeting = imap.welcome.decode()
    print(f"  Greeting: {greeting!r}")
    _assert("* OK" in greeting, "Greeting starts with * OK", greeting)

    # 1. CAPABILITY
    typ, caps_data = imap.capability()
    caps = caps_data[0].decode() if caps_data else ""
    print(f"  CAPABILITY: {caps}")
    _assert("IMAP4rev1" in caps, "CAPABILITY includes IMAP4rev1", caps)

    # 2. NOOP
    typ, _ = imap.noop()
    _assert(typ == "OK", "NOOP returns OK", typ)

    # 3. Bad-credential rejection
    rejected = False
    try:
        imap.login("nobody_bad_user", "wrong_password_xyz")
    except imaplib.IMAP4.error as e:
        rejected = True
        print(f"  Bad-login error: {str(e)[:80]}")
    _assert(rejected, "Bad credentials rejected")

    # 4. LIST before login — use raw send to bypass imaplib client-side state check
    tag_list = "A004"
    list_rsp = _send_raw(imap, tag_list, 'LIST "" "*"')
    print(f"  LIST-before-login response: {list_rsp!r}")
    _assert("NO" in list_rsp, "LIST before LOGIN returns NO", f"got {list_rsp!r}")

    # ------------------------------------------------------------------
    print("\n" + "=" * 62)
    print(" Phase B — Authenticated session (real DB-backed data)")
    print("=" * 62)
    # ------------------------------------------------------------------

    # Fresh connection for authenticated session
    imap2 = imaplib.IMAP4(host, port)

    # 5. Valid LOGIN
    try:
        typ, data2 = imap2.login(PROXY_USERNAME, PROXY_PASSWORD)
        print(f"  LOGIN {PROXY_USERNAME}: typ={typ} {data2}")
        _assert(typ == "OK", f"LOGIN({PROXY_USERNAME}) returns OK", f"got {typ}")
    except imaplib.IMAP4.error as e:
        _fail(f"LOGIN({PROXY_USERNAME}) raised error", str(e))
        print("  Cannot continue authenticated tests — aborting")
        return 1

    # 6. LIST — should show INBOX and Mindwall/Quarantine
    typ, mailbox_list = imap2.list()
    print(f"  LIST typ={typ}")
    raw_list = b"\n".join(m for m in mailbox_list if m).decode("utf-8", errors="replace")
    print(f"  LIST data:\n{raw_list}")
    _assert(typ == "OK", "LIST returns OK", f"got {typ}")
    _assert("INBOX" in raw_list, "LIST includes INBOX", raw_list)
    _assert(QUARANTINE_FOLDER in raw_list, f"LIST includes {QUARANTINE_FOLDER}", raw_list)

    # 7. SELECT INBOX — use readonly=True so imaplib sends EXAMINE (correct for read-only proxy)
    typ, select_data = imap2.select("INBOX", readonly=True)
    print(f"  SELECT INBOX: typ={typ} data={select_data}")
    _assert(typ == "OK", "SELECT INBOX returns OK", f"got {typ}")
    # EXISTS is reported during SELECT; imaplib exposes it as imap2.exists after select
    exists = int(select_data[0].decode()) if select_data and select_data[0] else 0
    print(f"  INBOX EXISTS={exists}")
    _assert(exists >= 1, "INBOX has at least one message", f"EXISTS={exists}")

    # 8. UID SEARCH ALL in INBOX
    typ, uid_data = imap2.uid("SEARCH", "ALL")
    print(f"  UID SEARCH ALL typ={typ} data={uid_data}")
    _assert(typ == "OK", "UID SEARCH ALL returns OK", f"got {typ}")
    uids_raw = uid_data[0].decode() if uid_data and uid_data[0] else ""
    uids = [u for u in uids_raw.split() if u.strip()]
    print(f"  UIDs found in INBOX: {uids}")
    _assert(len(uids) >= 1, "UID SEARCH ALL returns at least one UID", f"got {uids}")

    # 9. UID FETCH first UID — full attributes
    if uids:
        first_uid = uids[0]
        fetch_items = "(UID FLAGS RFC822.SIZE ENVELOPE BODY[])"
        typ, fetch_data = imap2.uid("FETCH", first_uid, fetch_items)
        print(f"  UID FETCH {first_uid} {fetch_items}: typ={typ}")
        _assert(typ == "OK", f"UID FETCH {first_uid} returns OK", f"got {typ}")

        # Reconstruct raw FETCH response as a string for inspection
        parts: list[str] = []
        for part in fetch_data:
            if isinstance(part, tuple):
                header = part[0].decode("utf-8", errors="replace")
                body_snippet = part[1][:200].decode("utf-8", errors="replace") if part[1] else ""
                parts.append(f"  header: {header}")
                parts.append(f"  body snippet: {body_snippet!r}")
            elif isinstance(part, bytes):
                parts.append(f"  bytes: {part[:120].decode('utf-8', errors='replace')!r}")
        fetch_text = "\n".join(parts)
        print(f"  FETCH response:\n{fetch_text}")
        _assert("UID" in fetch_text, f"FETCH response for UID {first_uid} contains UID token", "")
        _assert(any(kw in fetch_text for kw in ("FLAGS", "RFC822.SIZE", "ENVELOPE", "BODY")),
                "FETCH response contains expected attributes", fetch_text[:120])

    # 10. SELECT Mindwall/Quarantine
    typ, q_select_data = imap2.select(f'"{QUARANTINE_FOLDER}"', readonly=True)
    print(f"  SELECT {QUARANTINE_FOLDER}: typ={typ} data={q_select_data}")
    _assert(typ == "OK", f"SELECT {QUARANTINE_FOLDER} returns OK", f"got {typ}")
    q_exists = int(q_select_data[0].decode()) if q_select_data and q_select_data[0] else 0
    print(f"  {QUARANTINE_FOLDER} EXISTS={q_exists}")
    _assert(q_exists >= 1, f"{QUARANTINE_FOLDER} has at least one message", f"EXISTS={q_exists}")

    # 11. UID SEARCH ALL in quarantine
    typ, q_uid_data = imap2.uid("SEARCH", "ALL")
    print(f"  UID SEARCH ALL in quarantine: typ={typ} data={q_uid_data}")
    _assert(typ == "OK", "Quarantine UID SEARCH ALL returns OK", f"got {typ}")
    q_uids_raw = q_uid_data[0].decode() if q_uid_data and q_uid_data[0] else ""
    q_uids = [u for u in q_uids_raw.split() if u.strip()]
    print(f"  UIDs found in quarantine: {q_uids}")
    _assert(len(q_uids) >= 1, "Quarantine UID SEARCH returns at least one UID", f"got {q_uids}")

    # 12. UID FETCH quarantine message headers
    if q_uids:
        q_first = q_uids[0]
        typ, qfetch_data = imap2.uid("FETCH", q_first, "(UID FLAGS BODY[HEADER])")
        print(f"  UID FETCH {q_first} (quarantine headers): typ={typ}")
        _assert(typ == "OK", f"Quarantine UID FETCH {q_first} returns OK", f"got {typ}")
        qf_parts: list[str] = []
        for part in qfetch_data:
            if isinstance(part, tuple):
                qf_parts.append(part[0].decode("utf-8", errors="replace"))
                if part[1]:
                    qf_parts.append(part[1][:200].decode("utf-8", errors="replace"))
            elif isinstance(part, bytes):
                qf_parts.append(part[:120].decode("utf-8", errors="replace"))
        qf_text = " ".join(qf_parts)
        print(f"  Quarantine FETCH: {qf_text[:200]!r}")
        _assert(
            "[SEED] Quarantined" in qf_text or "Subject:" in qf_text,
            "Quarantine FETCH returns message headers",
            qf_text[:120],
        )

    # ------------------------------------------------------------------
    print("\n" + "=" * 62)
    print(" Phase C — Read-only mutation rejection")
    print("=" * 62)
    # ------------------------------------------------------------------

    # Re-select INBOX (readonly) so we can attempt mutations against a selected mailbox
    imap2.select("INBOX", readonly=True)

    # 13. STORE — mutation; should be rejected with NO [CANNOT]
    tag13 = "T013"
    store_rsp = _send_raw(imap2, tag13, "STORE 1 +FLAGS (\\Seen)")
    print(f"  STORE response: {store_rsp!r}")
    _assert(
        "NO" in store_rsp and ("CANNOT" in store_rsp or "read-only" in store_rsp.lower()
                                or "not supported" in store_rsp.lower()),
        "STORE rejected with NO [CANNOT]",
        store_rsp,
    )

    # 14. EXPUNGE — should be rejected
    tag14 = "T014"
    expunge_rsp = _send_raw(imap2, tag14, "EXPUNGE")
    print(f"  EXPUNGE response: {expunge_rsp!r}")
    _assert(
        "NO" in expunge_rsp,
        "EXPUNGE rejected with NO",
        expunge_rsp,
    )

    # 15. COPY — should be rejected
    if uids:
        tag15 = "T015"
        copy_rsp = _send_raw(imap2, tag15, f"UID COPY {uids[0]} INBOX.Trash")
        print(f"  UID COPY response: {copy_rsp!r}")
        _assert("NO" in copy_rsp, "UID COPY rejected with NO", copy_rsp)

    # 16. APPEND — should be rejected
    tag16 = "T016"
    append_rsp = _send_raw(imap2, tag16, "APPEND INBOX {5}\r\nHello")
    print(f"  APPEND response: {append_rsp!r}")
    _assert("NO" in append_rsp, "APPEND rejected with NO", append_rsp)

    # Tidy logout
    try:
        imap2.logout()
    except imaplib.IMAP4.error:
        pass

    # ------------------------------------------------------------------
    print("\n" + "=" * 62)
    if _failures:
        print(f" RESULT: {len(_failures)} failure(s)")
        for f in _failures:
            print(f"   - {f}")
        print("=" * 62)
        return 1
    else:
        print(" Manual IMAP proxy verification — ALL CHECKS PASSED")
        print("=" * 62)
        return 0


if __name__ == "__main__":
    sys.exit(main())
