# Security Policy

## Overview

Mindwall is designed as a privacy-first, self-hosted platform. Security is a core product concern, not an afterthought. This document describes how to report vulnerabilities, what to expect from the response process, and how the project handles sensitive information.

---

## Supported versions

Mindwall is under active development. Security fixes are applied to the **current main branch** only. There are no published release branches or versioned LTS tracks at this stage.

| Version | Support status |
|---------|---------------|
| `main` (latest) | ✅ Supported — security fixes applied |
| Any older fork or snapshot | ❌ Not supported |

If you are running a deployment from a snapshot or fork, ensure you are tracking the latest upstream code.

---

## Reporting a vulnerability

**Do not open a public GitHub issue to report a security vulnerability.**

Vulnerabilities disclosed publicly before a fix is available put all users at risk.

### How to report

Send a report by email to the project maintainer. Include as much of the following as you can:

1. **Description** — what is the vulnerability and what does it allow an attacker to do?
2. **Component** — which module, route, or system component is affected?
3. **Reproduction steps** — a minimal, clear set of steps to reproduce the issue.
4. **Impact assessment** — what is the potential severity in your estimation?
5. **Affected versions** — which commit or deployment version did you observe this on?
6. **Any suggested fix** — if you have a proposed mitigation or patch, include it.

If you are unsure whether something qualifies as a security vulnerability, report it as one. It is better to over-report than to leave a real issue unaddressed.

### What to include in your report

Useful supporting material:

- Request/response captures (with credentials or sensitive data redacted)
- Log excerpts
- Proof-of-concept code (responsible, not weaponized)
- CVE references if related to a dependency vulnerability

### What to omit from public channels

Until a fix is available and a disclosure window has passed, do **not** post any of the following in public issues, forums, or pull requests:

- The existence of the vulnerability
- Proof-of-concept exploit code
- Specific affected endpoints, parameters, or logic paths
- Any message content, credential data, or user data from a real deployment

---

## Response process

1. **Acknowledgement** — You will receive an acknowledgement within 5 business days confirming the report was received.
2. **Triage** — The report will be reviewed, reproduced, and assessed for severity within 10 business days.
3. **Remediation** — A fix will be prepared. For critical issues, this is prioritized immediately.
4. **Notification** — You will be notified when the fix is merged. You may be asked to verify the fix if you are able to do so.
5. **Disclosure** — After the fix is available, coordinated public disclosure is welcomed. The timeline will be agreed with the reporter.

For critical vulnerabilities (data exfiltration, authentication bypass, credential exposure), the target remediation window is **72 hours** from confirmed triage.

---

## Secrets and credential handling expectations

Mindwall stores sensitive data. These are the security expectations for operators:

### `SECRET_KEY`
- Used to sign session cookies.
- Must be at least 32 characters of high entropy.
- Rotation invalidates all active sessions — plan accordingly.
- Must never appear in application logs, error output, or HTTP responses.

### `ENCRYPTION_KEY`
- A Fernet key used to encrypt upstream IMAP/SMTP passwords at rest.
- Loss of this key means upstream credentials cannot be decrypted — all mailbox profiles must be re-registered.
- Rotation requires re-encrypting all stored credentials — a migration utility will be provided in a future phase.
- Must never appear in application logs, error output, or HTTP responses.

### Upstream IMAP/SMTP passwords
- Encrypted in the database using the `ENCRYPTION_KEY`.
- Decrypted in memory only when needed for upstream connectivity or sync.
- Never logged, never exposed in API responses or admin UI.

### Proxy passwords
- Stored as bcrypt hashes only.
- Cannot be recovered if lost — the user must reset via the admin UI.
- Never logged, never returned via any API endpoint.

### Deployment expectations
- Run Mindwall behind a reverse proxy (nginx, Caddy, etc.) with TLS termination for the web UI.
- Do not expose the IMAP proxy (port 1993/1143) or SMTP proxy (port 1587) to the public internet without TLS.
- Restrict access to the PostgreSQL and Redis ports to internal network only.
- Store `.env` files outside version control; use Docker secrets or an external secret manager in production.

---

## Privacy guarantees

Mindwall is built around a hard requirement that **no message data leaves the deployment boundary**. These guarantees are enforced at the code level:

- The Ollama client (`app/analysis/ollama_client.py`) enforces localhost-only connections. It will refuse to connect to any non-localhost endpoint.
- No external HTTP calls are made with message content, headers, or prompts.
- No telemetry, analytics, or crash reporting collects or transmits message data.
- HTML rendering strips all remote resources (images, scripts, stylesheets) before displaying message content.

If you discover any code path that sends message data outside the server, this is a **critical security vulnerability** and should be reported immediately using the process above.

---

## Dependency vulnerabilities

If you discover a vulnerability in one of Mindwall's dependencies (listed in `pyproject.toml`), please:

1. Check whether the vulnerability is already patched in a newer version.
2. If it is, open a normal issue or pull request referencing the CVE and the required version update.
3. If the vulnerability is unpatched upstream and has a meaningful impact on Mindwall's security posture, report it using the private disclosure process above.

---

## Scope

The following are in scope for security reports:

- Authentication and session management (`app/auth/`)
- Credential encryption and storage (`app/security/crypto.py`, `app/mailboxes/`)
- IMAP proxy authentication and data access controls (`app/proxies/imap/`)
- SMTP proxy authentication and delivery controls (`app/proxies/smtp/`)
- Quarantine access controls and release workflows (`app/quarantine/`)
- Admin route authorization (`app/admin/`, `app/dependencies.py`)
- HTML rendering and sanitization (`app/messages/html_safe.py`, quarantine preview templates)
- Any code path that could cause message data to be sent outside the server
- Injection vulnerabilities (SQL injection, template injection, command injection)
- Path traversal in file storage operations

The following are **out of scope**:

- Vulnerabilities in the underlying OS or Docker runtime
- Denial-of-service attacks that require physical or network-level access to the server
- Social engineering of project maintainers
- Vulnerabilities in Ollama or Llama 3.1 that are not amplified by Mindwall's integration

---

## Hall of fame

Security researchers who responsibly disclose valid vulnerabilities will be acknowledged in this section (with their permission).

*No entries yet.*
