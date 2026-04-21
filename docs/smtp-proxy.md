# SMTP proxy

Mindwall's SMTP proxy accepts outbound mail submissions from your mail client using Mindwall proxy credentials, and either stores the message locally (capture mode) or forwards it to your upstream SMTP server (relay mode).

---

## Architecture

```
Mail client (Thunderbird, Apple Mail, etc.)
        │
        │  SMTP submission (TCP, port 1587)
        │
        ▼
SmtpServer (app/proxies/smtp/server.py)
        │
        ├── SmtpConnection (one asyncio.Task per TCP connection)
        │       │
        │       ├── AUTH PLAIN / AUTH LOGIN
        │       │   → authenticate_smtp_credentials()
        │       │     (app/proxies/smtp/session.py)
        │       │
        │       └── DATA → deliver_outbound()
        │                  (app/proxies/smtp/delivery.py)
        │                     │
        │                     ├── capture: write .eml to disk + persist OutboundMessage
        │                     └── relay:   SmtpRelayClient → upstream SMTP
        │                                  (app/proxies/smtp/relay.py)
        │
        └── AsyncSession (fresh per connection)
```

The SMTP proxy is started as a standalone process by `workers/smtp_proxy.py`. It is independent of the FastAPI web application.

---

## Supported commands

### Any state

| Command | Behaviour |
|---------|-----------|
| `QUIT` | Sends `221 2.0.0 Bye` and closes |
| `NOOP` | Responds `250 2.0.0 OK` |

### Pre-authentication

| Command | Behaviour |
|---------|-----------|
| `EHLO <domain>` | Responds with capability list: `250-EHLO`, `250-AUTH PLAIN LOGIN`, `250-SIZE <max>`, `250 OK` |
| `HELO <domain>` | Accepted for compatibility; client is implicitly set as EHLO-less |
| `AUTH PLAIN <credentials>` | One-step BASE64-encoded `\0user\0password` |
| `AUTH LOGIN` | Two-step challenge/response: username then password, each BASE64-encoded |

### Post-authentication

| Command | Behaviour |
|---------|-----------|
| `EHLO <domain>` | Also accepted post-auth for pipelining clients |
| `MAIL FROM:<address>` | Sets envelope sender |
| `RCPT TO:<address>` | Adds an envelope recipient (multiple RCPT TO accepted) |
| `DATA` | Initiates message body transfer; terminated by `\r\n.\r\n` |
| `RSET` | Resets the envelope (clears MAIL FROM and RCPT TO); auth is preserved |

### Unsupported commands

`VRFY`, `EXPN`, `TURN`, `ETRN`, and any extension not advertised in `EHLO` return `502 5.5.1 Command not implemented`.

---

## Delivery modes

### Capture mode (default)

When `SMTP_DELIVERY_MODE=capture`:

1. The raw message is written to `OUTBOUND_MESSAGE_STORE_PATH/<sha256[:2]>/<sha256>.eml`
2. An `OutboundMessage` record is persisted to the database
3. The client receives `250 2.0.0 Message accepted for delivery`
4. Nothing is forwarded to any external server

Captured messages are visible in the admin UI at `/admin/outbound/`.

### Relay mode

When `SMTP_DELIVERY_MODE=relay`:

1. The raw message is still written to disk and a database record created
2. An `SmtpRelayClient` connects to the upstream SMTP server using the stored encrypted credentials for the authenticated Mindwall user
3. The message is relayed via `MAIL FROM` + `RCPT TO` + `DATA`
4. On success, the `OutboundMessage` status is set to `relayed`
5. On failure, the status is set to `failed` and the error is recorded

> **Note:** Relay mode requires the mailbox profile to have valid upstream SMTP credentials stored. If the credentials cannot be decrypted or the connection fails, delivery fails with a `451` transient error.

---

## Security

- **Authentication required.** `MAIL FROM` is rejected with `530 5.7.0 Authentication required` if the client has not authenticated.
- **No credential echoing.** Auth credentials are never reflected in protocol responses or logs.
- **Message size limit.** Messages exceeding `SMTP_MAX_MESSAGE_BYTES` (default 25 MB) are rejected with `552 5.3.4 Message size exceeds limit`.
- **Line length limit.** Individual command lines are limited to prevent resource exhaustion.
- **No TLS.** The proxy listens on plaintext TCP. Use on `localhost` or a trusted private network. STARTTLS is planned for Phase 11.

---

## OutboundMessage record

Every submission creates an `OutboundMessage` row:

| Field | Description |
|-------|-------------|
| `mailbox_profile_id` | Which Mindwall user submitted this |
| `mail_from` | Envelope sender |
| `rcpt_to` | JSON list of envelope recipients |
| `subject` | Extracted from `Subject:` header |
| `raw_size` | Message size in bytes |
| `sha256` | SHA-256 of the raw message |
| `delivery_mode` | `capture` or `relay` |
| `status` | `captured`, `relayed`, `failed` |
| `error_message` | Relay failure reason (if applicable) |
| `eml_path` | Filesystem path to the stored `.eml` file |

---

## Running the proxy

### Local development

```bash
# Default settings
python workers/smtp_proxy.py

# Custom settings
SMTP_PROXY_PORT=1587 SMTP_DELIVERY_MODE=capture python workers/smtp_proxy.py
```

### Docker

The `smtp_proxy` service runs automatically with `docker compose up -d`. Host port is configurable via `SMTP_PROXY_DEV_PORT` (default: `1587`).

```bash
docker compose --env-file .env.docker up -d smtp_proxy
docker compose --env-file .env.docker logs -f smtp_proxy
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_PROXY_HOST` | `0.0.0.0` | Bind address |
| `SMTP_PROXY_PORT` | `1587` | TCP listen port |
| `SMTP_PROXY_DISPLAY_HOST` | `127.0.0.1` | Hostname shown in the proxy setup instructions UI |
| `SMTP_DELIVERY_MODE` | `capture` | `capture` or `relay` |
| `SMTP_RELAY_TIMEOUT_SECONDS` | `30` | Upstream relay connection timeout |
| `SMTP_MAX_MESSAGE_BYTES` | `26214400` | Maximum accepted message size (25 MB) |
| `OUTBOUND_MESSAGE_STORE_PATH` | `./data/outbound_messages` | Root directory for captured `.eml` files |

---

## Verification

```bash
# Run the 22-assertion verification script against a running proxy
python scripts/verify_smtp_proxy.py
```

The script tests:
- TCP connection and server greeting
- EHLO capability negotiation
- AUTH PLAIN and AUTH LOGIN with valid and invalid credentials
- MAIL FROM and RCPT TO enforcement
- DATA transaction and message acceptance
- RSET behavior
- NOOP and QUIT
- Message size limit enforcement
- Database persistence of `OutboundMessage`

---

## Testing manually with smtplib

```python
import smtplib, base64

with smtplib.SMTP("localhost", 1587) as smtp:
    smtp.ehlo("test.example.com")
    smtp.login("mw_youruser_abc123", "your-proxy-password")
    smtp.sendmail(
        "from@example.com",
        ["to@example.com"],
        "Subject: Test\r\n\r\nHello from Mindwall.",
    )
    print("Message submitted.")
```

---

## Mail client setup

See [Mailbox onboarding](mailbox-onboarding.md) for per-client configuration examples.

Typical settings:

| Setting | Value |
|---------|-------|
| Outgoing server | `127.0.0.1` (or `SMTP_PROXY_DISPLAY_HOST`) |
| Port | `1587` |
| Security | None (no SSL/TLS) |
| Authentication | Normal password |
| Username | Your Mindwall proxy username |
| Password | Your Mindwall proxy password |
