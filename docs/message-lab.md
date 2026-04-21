# Message Lab

The Message Lab is an admin-only tool for manually uploading, inspecting, and analyzing raw email files. It is useful for testing the analysis pipeline, debugging parse issues, and verifying how specific messages are handled.

---

## Enabling the Message Lab

The Message Lab is enabled by default. To disable it:

```env
MESSAGE_LAB_ENABLED=false
```

When disabled, all `/admin/messages/` routes return 404.

---

## Uploading a message

**URL:** `/admin/messages/`

1. Log in with an `admin` role account.
2. Navigate to **Admin → Message Lab**.
3. Click **Upload .eml** and select a raw email file.
4. The maximum upload size is `MESSAGE_LAB_MAX_UPLOAD_MB` MB (default: 25 MB).

On upload, Mindwall:
1. Parses the raw bytes using the RFC 5322 parser
2. Computes the SHA-256 of the raw content
3. Writes the `.eml` to `RAW_MESSAGE_STORE_PATH` using two-char prefix layout
4. Persists a `Message` record with envelope fields, URL count, and attachment count
5. Redirects to the message detail page

---

## Message detail page

**URL:** `/admin/messages/{message_id}`

The detail page shows the parsed message:

| Section | Contents |
|---------|---------|
| Envelope | From, To, CC, Reply-To, Subject, Date, Message-ID |
| Authentication headers | DKIM-Signature, Received-SPF, Authentication-Results (if present) |
| Body preview | Plain-text body (truncated to 2000 chars) |
| HTML body indicator | Whether an HTML part was present |
| Extracted URLs | All HTTP/HTTPS URLs extracted from body and HTML anchors |
| Attachments | Filename, content type, size for each attachment |
| Storage info | SHA-256, file path, raw size |

---

## Running analysis

On the message detail page, click **Analyse** to run the full analysis pipeline against the uploaded message.

This runs:
1. Deterministic security checks
2. Ollama LLM analysis (if `LLM_ENABLED=true`)
3. Score combination and verdict computation
4. Persistence of `AnalysisRun` and `DimensionScore` rows

After analysis, the page refreshes and shows:

| Section | Contents |
|---------|---------|
| Verdict badge | `allow` / `allow_with_banner` / `soft_hold` / `quarantine` / `escalate_to_admin` |
| Risk score | Overall risk (0.0–1.0) |
| Confidence | Model confidence (0.0–1.0) |
| Degraded indicator | Yellow banner if Ollama was unavailable or returned invalid output |
| Dimension scores | 12-cell grid, color-coded by score |
| Deterministic findings | Each finding with check ID, dimension, severity, description, and evidence |
| LLM rationale | Model-generated summary (if available) |
| Evidence list | Specific evidence strings extracted by the model |
| Recommended action | Model-suggested action |

---

## Raw message storage layout

```
RAW_MESSAGE_STORE_PATH/
    ab/
        ab3f4e... (sha256).eml
    cd/
        cd7e2a... (sha256).eml
```

The two-character prefix is the first two hex digits of the SHA-256 hash. This distributes files across 256 subdirectories to avoid filesystem performance degradation with large collections.

Writes are idempotent — uploading the same file twice produces the same path and SHA-256; the existing file is not overwritten.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MESSAGE_LAB_ENABLED` | `true` | Enable or disable the Message Lab routes |
| `MESSAGE_LAB_MAX_UPLOAD_MB` | `25` | Maximum `.eml` upload size in megabytes |
| `RAW_MESSAGE_STORE_PATH` | `./data/raw_messages` | Root directory for raw `.eml` file storage |

---

## Use cases

| Scenario | Steps |
|---------|-------|
| Test a suspicious `.eml` before exposing it to users | Upload → Analyse → check verdict and scores |
| Debug a false positive | Upload the misidentified message → Analyse → review deterministic findings and LLM reasoning |
| Verify analysis pipeline changes | Upload test fixtures before and after code changes → compare verdicts |
| Confirm Ollama integration is working | Upload any message → Analyse → check that LLM section is populated (not degraded) |
