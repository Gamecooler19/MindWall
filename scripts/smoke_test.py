"""Docker smoke test - authenticated end-to-end flow.

Run with:  conda run -n mindwall python scripts/smoke_test.py
Targets the Dockerized app at http://localhost:8000.

Credentials match .env.docker defaults:
    MINDWALL_ADMIN_EMAIL    = admin@mindwall.local
    MINDWALL_ADMIN_PASSWORD = changeme-dev-only
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

BASE = "http://localhost:8000"
EMAIL = "admin@mindwall.local"
PASSWORD = "changeme-dev-only"
EML_FILE = Path(__file__).parent.parent / "tests" / "fixtures" / "emails" / "plain_text.eml"

PASS_TAG = "\033[32mPASS\033[0m"
FAIL_TAG = "\033[31mFAIL\033[0m"

results: list[tuple[str, bool, str]] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    tag = PASS_TAG if cond else FAIL_TAG
    print(f"  [{tag}] {label}" + (f"  ({detail})" if detail else ""))
    results.append((label, cond, detail))


def main() -> int:
    client = httpx.Client(base_url=BASE, follow_redirects=True, timeout=20)

    print("\n=== Mindwall Docker Smoke Test ===\n")

    # Health
    print("[1] Health checks")
    r = client.get("/health/live")
    check("GET /health/live -> 200", r.status_code == 200, r.text[:80])
    r = client.get("/health/ready")
    check("GET /health/ready -> 200", r.status_code == 200, r.text[:120])
    check("  database ok", '"database":"ok"' in r.text)
    check("  redis ok", '"redis":"ok"' in r.text)

    # Login page
    print("\n[2] Login page (unauthenticated)")
    r = client.get("/login")
    check("GET /login -> 200", r.status_code == 200, f"len={len(r.text)}")
    check("  login form present", "password" in r.text.lower())

    # Authenticate
    print("\n[3] Authenticate")
    r = client.post("/login", data={"email": EMAIL, "password": PASSWORD})
    check("POST /login -> 200 after redirect", r.status_code == 200, r.url.path)
    check("  redirected away from /login", r.url.path != "/login")
    authed = r.status_code == 200 and r.url.path != "/login"

    if not authed:
        print("\n  Authentication failed - cannot run authenticated tests.")
        print(f"  Body: {r.text[:400]}")
        return _report()

    # Admin dashboard
    print("\n[4] Admin dashboard")
    r = client.get("/admin/")
    check("GET /admin/ -> 200", r.status_code == 200, f"len={len(r.text)}")
    check("  dashboard content", "mindwall" in r.text.lower() or "dashboard" in r.text.lower())

    # Messages list
    print("\n[5] Messages list")
    r = client.get("/admin/messages/")
    check("GET /admin/messages/ -> 200", r.status_code == 200, f"len={len(r.text)}")

    # Message upload page
    print("\n[6] Message Lab upload page")
    r = client.get("/admin/messages/upload")
    check("GET /admin/messages/upload -> 200", r.status_code == 200, f"len={len(r.text)}")

    # Upload EML
    msg_id = None
    if EML_FILE.exists():
        with EML_FILE.open("rb") as fh:
            r = client.post(
                "/admin/messages/upload",
                files={"eml_file": (EML_FILE.name, fh, "message/rfc822")},
            )
        check(f"POST upload {EML_FILE.name} -> 200", r.status_code == 200, f"url={r.url.path}")
        if r.status_code == 200 and r.url.path.startswith("/admin/messages/"):
            try:
                msg_id = int(r.url.path.rstrip("/").split("/")[-1])
            except (ValueError, IndexError):
                pass
        check("  redirected to message detail", msg_id is not None, f"url={r.url.path}")
    else:
        check(f"EML fixture exists ({EML_FILE.name})", False, str(EML_FILE))

    # Message detail + analysis
    if msg_id is not None:
        print(f"\n[7] Message detail (id={msg_id})")
        r = client.get(f"/admin/messages/{msg_id}")
        check(f"GET /admin/messages/{msg_id} -> 200", r.status_code == 200, f"len={len(r.text)}")
        check("  message content visible", any(
            k in r.text.lower() for k in ("subject", "from", "sender", "date", "body")
        ))

        print(f"\n[8] Trigger analysis (id={msg_id})")
        r = client.post(f"/admin/messages/{msg_id}/analyze")
        check("POST analyze -> 200", r.status_code == 200, f"url={r.url.path}")
        check("  analysis content visible", any(
            k in r.text.lower() for k in ("risk", "score", "verdict", "analysis", "confidence")
        ))
    else:
        print("\n[7-8] Skipped (no message uploaded)")

    # Quarantine inbox
    print("\n[9] Quarantine inbox")
    r = client.get("/admin/quarantine/")
    check("GET /admin/quarantine/ -> 200", r.status_code == 200, f"len={len(r.text)}")
    check("  quarantine content", "quarantine" in r.text.lower())

    # Mailboxes
    print("\n[10] Mailboxes list")
    r = client.get("/mailboxes/")
    check("GET /mailboxes/ -> 200", r.status_code == 200, f"len={len(r.text)}")

    # Logout
    print("\n[11] Logout")
    r = client.post("/logout")
    check("POST /logout -> redirect to /login", r.url.path == "/login", r.url.path)

    return _report()


def _report() -> int:
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed
    print(f"\n{'='*45}")
    print(f"Results: {passed}/{total} passed  ({failed} failed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
