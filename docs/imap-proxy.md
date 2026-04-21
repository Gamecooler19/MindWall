# IMAP proxy

Mindwall's IMAP proxy is a read-only RFC 3501-compatible server that your mail client connects to instead of your upstream IMAP server. It authenticates with Mindwall proxy credentials and presents a filtered view of the local message store.

---

## Architecture

```
Mail client (Thunderbird, Apple Mail, etc.)
        │
        │  IMAP (TCP, port 1993 / 1143 in Docker dev)
        │
        ▼
ImapServer (app/proxies/imap/server.py)
        │
        ├── ImapConnection (one asyncio.Task per TCP connection)
        │       │
        │       ├── authenticate_proxy_credentials()  → MailboxProfile
        │       │   (app/proxies/imap/session.py)
        │       │
        │       └── ImapMailbox (app/proxies/imap/mailbox.py)
        │               │
        │               ├── INBOX → MailboxItem (visibility=VISIBLE)
        │               └── Mindwall/Quarantine → MailboxItem (visibility=QUARANTINED|HIDDEN)
        │
        └── AsyncSession (fresh per connection, scoped to connection lifetime)
```

The proxy server is started as a standalone process by `workers/imap_proxy.py`. It is independent of the FastAPI web application.

---

## Supported commands

### Any state (before and after authentication)

| Command | Behaviour |
|---------|-----------|
| `CAPABILITY` | Returns server capabilities: `IMAP4rev1 AUTH=PLAIN IDLE LITERAL+` |
| `NOOP` | Always responds `OK` |
| `LOGOUT` | Sends `BYE` and closes the connection |

### Not-authenticated state

| Command | Behaviour |
|---------|-----------|
| `LOGIN user pass` | Validates Mindwall proxy credentials against the database. On success, transitions to authenticated state |

`AUTHENTICATE` is not supported — `LOGIN` only.

### Authenticated state

| Command | Behaviour |
|---------|-----------|
| `LIST "" *` | Returns the two virtual folders: `INBOX` and `Mindwall/Quarantine` |
| `SELECT INBOX` | Selects INBOX — returns EXISTS, RECENT, FLAGS, PERMANENTFLAGS, UIDNEXT, UIDVALIDITY |
| `SELECT Mindwall/Quarantine` | Selects the quarantine virtual folder |
| `EXAMINE <folder>` | Identical to SELECT — always read-only |
| `STATUS <folder> (MESSAGES RECENT UNSEEN)` | Returns status counts for the specified folder |
| `SEARCH <criteria>` | Supports `ALL`, `SEEN`, `UNSEEN`, sequence-set |
| `UID SEARCH <criteria>` | UID-based variant of SEARCH |
| `FETCH <seq> <items>` | Fetches envelope data, flags, RFC822, RFC822.SIZE, BODY, BODYSTRUCTURE |
| `UID FETCH <uid> <items>` | UID-based variant of FETCH |
| `CLOSE` | Deselects the current mailbox, returns to authenticated state |

### Rejected commands (mutation)

The following commands return `NO [CANNOT] This proxy is read-only`:

`STORE`, `COPY`, `APPEND`, `EXPUNGE`, `CREATE`, `DELETE`, `RENAME`, `MOVE`,
`SUBSCRIBE`, `UNSUBSCRIBE`, `LSUB`, `SETFLAGS`, `SETANNOTATION`

Any unrecognized command returns `BAD Command not recognized`.

---

## Virtual folders

| Folder | Contents | Backed by |
|--------|----------|-----------|
| `INBOX` | Cleared messages | `MailboxItem` where `visibility = VISIBLE` |
| `Mindwall/Quarantine` | Flagged messages | `MailboxItem` where `visibility IN (QUARANTINED, HIDDEN)` |

UIDs in the virtual folders are stable sequential integers assigned by Mindwall. They do not correspond to upstream UIDs. UIDVALIDITY is based on the mailbox profile ID.

---

## Authentication

The proxy uses the `LOGIN` command. The username and password are validated against the `mailbox_profiles` table using the same bcrypt hash comparison used by the web UI.

Authentication is defined in `app/proxies/imap/session.py`:

```python
async def authenticate_proxy_credentials(
    db: AsyncSession,
    proxy_username: str,
    proxy_password: str,
) -> ProxySession | None: ...
```

Returns `None` if credentials are invalid. After three consecutive failures on a connection, the connection is closed.

---

## Limitations (current release)

- **Read-only.** No mutation commands are supported.
- **No TLS.** The proxy listens on plaintext TCP. Use on `localhost` or a trusted private network only. STARTTLS is planned for Phase 11.
- **No pipelining.** Commands are processed one at a time.
- **Single folder synced.** Only `INBOX` is synced from upstream; multi-folder support is planned.
- **No IDLE.** Push-based update notifications are not implemented.
- **No flags persistence.** Flags returned are derived from the message's analysis status; user flag changes (e.g. `\Seen`) are not stored.

---

## Running the proxy

### Local development

```bash
# Default port (1993)
python workers/imap_proxy.py

# Custom port
IMAP_PROXY_PORT=1143 python workers/imap_proxy.py
```

### Docker

The `imap_proxy` service in `docker-compose.yml` runs automatically. Host port is configurable via `IMAP_PROXY_DEV_PORT` (default: `1143`).

```bash
docker compose --env-file .env.docker up -d imap_proxy
docker compose --env-file .env.docker logs -f imap_proxy
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `IMAP_PROXY_HOST` | `0.0.0.0` | Bind address |
| `IMAP_PROXY_PORT` | `1993` | TCP listen port |
| `IMAP_PROXY_DISPLAY_HOST` | `127.0.0.1` | Hostname shown in the proxy setup instructions UI |

---

## Verification

```bash
# Run the 17-assertion verification script against a running proxy
python scripts/verify_imap_proxy.py
```

This script tests:
- TCP connection and server greeting
- CAPABILITY response
- Login with valid and invalid credentials
- LIST folder enumeration
- SELECT INBOX and Mindwall/Quarantine
- UID SEARCH ALL
- FETCH on available messages
- Mutation command rejection (STORE, COPY, EXPUNGE)
- LOGOUT

---

## Mail client setup

See [Mailbox onboarding](mailbox-onboarding.md) for per-client configuration examples (Apple Mail, Thunderbird).

Typical settings:

| Setting | Value |
|---------|-------|
| Server | `127.0.0.1` (or `IMAP_PROXY_DISPLAY_HOST`) |
| Port | `1993` (local) or `1143` (Docker dev) |
| Security | None (no SSL/TLS) |
| Authentication | Normal password |
| Username | Your Mindwall proxy username |
| Password | Your Mindwall proxy password |
