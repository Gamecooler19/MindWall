# Mailbox onboarding

This guide explains how to register a mailbox with Mindwall and configure a mail client to use the Mindwall proxy.

---

## Overview

Mindwall works by intercepting mail at the protocol level. Instead of connecting your mail client directly to Gmail, Fastmail, or your corporate IMAP server, you point it at Mindwall. Mindwall then:

1. Authenticates you with Mindwall-issued proxy credentials
2. Presents a filtered view of your mailbox backed by the local Mindwall message store
3. Accepts outbound submissions and either captures or relays them

Your upstream mailbox remains unchanged and continues to receive your real mail — Mindwall pulls from it in the background and enforces policy locally.

---

## Registering a mailbox

### 1. Log in and navigate

Log in to the Mindwall web UI and go to **Mailboxes** → **Register new mailbox** (or `/mailboxes/new`).

### 2. Fill in the registration form

**General:**

| Field | Example | Notes |
|-------|---------|-------|
| Display name | `Work — Gmail` | Friendly name for this mailbox |
| Email address | `you@gmail.com` | The upstream mailbox address |

**Upstream IMAP (incoming):**

| Field | Gmail example | Notes |
|-------|--------------|-------|
| IMAP host | `imap.gmail.com` | |
| IMAP port | `993` | |
| IMAP username | `you@gmail.com` | |
| IMAP password | `your-app-password` | Use an app-specific password where available |
| Security | `ssl` | Options: `ssl`, `starttls`, `plain` |

**Upstream SMTP (outgoing):**

| Field | Gmail example | Notes |
|-------|--------------|-------|
| SMTP host | `smtp.gmail.com` | |
| SMTP port | `465` | |
| SMTP username | `you@gmail.com` | |
| SMTP password | `your-app-password` | Same as IMAP password in most cases |
| Security | `ssl` | Options: `ssl`, `starttls`, `plain` |

### 3. Submit the form

Mindwall will:
1. Validate connectivity to both IMAP and SMTP servers
2. Encrypt your upstream credentials with the server-side Fernet key
3. Generate a unique Mindwall proxy username and a random proxy password
4. Show you the proxy password **once** — copy it immediately

> **Important:** The proxy password is stored only as a bcrypt hash. It cannot be recovered. If you lose it, use **Reset proxy password** on the mailbox detail page.

---

## Configuring your mail client

After registration, the mailbox detail page shows your proxy credentials and connection settings:

| Setting | Value |
|---------|-------|
| Incoming server (IMAP) | `IMAP_PROXY_DISPLAY_HOST` (default: `127.0.0.1`) |
| Incoming port | `IMAP_PROXY_PORT` (default: `1993`; Docker dev stack: `1143`) |
| Outgoing server (SMTP) | `SMTP_PROXY_DISPLAY_HOST` (default: `127.0.0.1`) |
| Outgoing port | `SMTP_PROXY_PORT` (default: `1587`) |
| Username | Your Mindwall proxy username (format: `mw_<name>_<suffix>`) |
| Password | Your Mindwall proxy password (saved from registration) |
| Security | None (plaintext connection to localhost) |

> **Security note:** The proxy listener is unencrypted in the current release. It is intended for use on `localhost` or within a trusted private network. Do not expose the proxy ports to the public internet without additional TLS termination. STARTTLS support is planned.

### Example: Apple Mail

1. **Mail → Add Account → Other Mail Account**
2. Name: anything descriptive
3. Email: your real upstream email address
4. Incoming: IMAP, host = `127.0.0.1`, port = `1993`, username = `mw_...`, password = `your-proxy-password`, SSL = off
5. Outgoing: SMTP, host = `127.0.0.1`, port = `1587`, username = `mw_...`, password = `your-proxy-password`, SSL = off

### Example: Thunderbird

1. **Edit → Account Settings → Add Mail Account**
2. Enter your name and email address
3. On the next screen, click **Configure manually**
4. Incoming: IMAP, server = `127.0.0.1`, port = `1993`, connection security = none, authentication = normal password, username = `mw_...`
5. Outgoing: SMTP, server = `127.0.0.1`, port = `1587`, connection security = none, authentication = normal password, username = `mw_...`

---

## After registration

### Trigger an initial sync

Your mail client will see an empty mailbox until Mindwall pulls messages from upstream. Trigger the first sync:

1. Go to **Admin → Mailboxes → [your mailbox] → Sync**
2. Click **Sync Now**
3. Watch the status update — messages are ingested, analyzed, and made visible

After sync, your mail client should show messages in `INBOX` (cleared messages) and optionally in `Mindwall/Quarantine` (flagged messages).

### Reset proxy password

If you lose your proxy password:

1. Go to **Mailboxes → [your mailbox]**
2. Click **Reset proxy password**
3. A new password is generated and shown once

---

## Managing mailboxes

| Action | URL |
|--------|-----|
| List all mailboxes | `/mailboxes/` |
| Register new mailbox | `/mailboxes/new` |
| View mailbox details / proxy instructions | `/mailboxes/{id}` |
| Edit upstream settings | `/mailboxes/{id}/edit` |
| Test connectivity | `/mailboxes/{id}/test` (POST) |
| Reset proxy password | `/mailboxes/{id}/reset-password` (POST) |
| Delete mailbox | `/mailboxes/{id}/delete` (POST) |

> **Note:** Deleting a mailbox is permanent and removes all associated sync state, mailbox items, and messages from the local Mindwall store. Upstream mail is unaffected.

---

## Credential security

- **Upstream passwords** are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256). They are decrypted in memory only when establishing upstream connections during sync.
- **Proxy passwords** are stored as bcrypt hashes only. They cannot be retrieved.
- **No credentials** are ever logged, returned by API responses, or visible after the single reveal at registration.
- Mindwall proxy credentials are independent of upstream credentials — compromising one does not expose the other.
