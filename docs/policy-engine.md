# Policy engine

The policy engine converts the combined analysis risk score and confidence into a final verdict for each message. It is the authoritative decision-maker between the analysis engine and the quarantine/delivery systems.

---

## Verdict definitions

| Verdict | Identifier | Description |
|---------|-----------|-------------|
| Allow | `allow` | Low risk — deliver normally |
| Allow with banner | `allow_with_banner` | Moderate risk — deliver with a warning annotation |
| Soft hold | `soft_hold` | Elevated risk — temporarily held; can be released automatically or after review |
| Quarantine | `quarantine` | High risk — moved to quarantine; requires admin review |
| Escalate to admin | `escalate_to_admin` | Very high risk — quarantined and an alert is raised immediately |
| Reject | `reject` | Gateway mode only — message is refused before delivery |

Verdicts are defined in `app/policies/constants.py` as the `Verdict` enum.

---

## Risk thresholds

The verdict is determined by the lowest threshold that the overall risk score exceeds:

```
risk ≤ 0.25  →  allow
risk ≤ 0.45  →  allow_with_banner
risk ≤ 0.65  →  soft_hold
risk ≤ 0.85  →  quarantine
risk > 0.85  →  escalate_to_admin
```

These defaults are set via environment variables and can be overridden at runtime via the Policy Editor (`/admin/policy/`).

| Environment variable | Default |
|---------------------|---------|
| `VERDICT_THRESHOLD_ALLOW` | `0.25` |
| `VERDICT_THRESHOLD_ALLOW_WITH_BANNER` | `0.45` |
| `VERDICT_THRESHOLD_SOFT_HOLD` | `0.65` |
| `VERDICT_THRESHOLD_QUARANTINE` | `0.85` |

---

## Risk score computation

The overall risk score combines the deterministic check result and the LLM result:

```
overall_risk = 0.4 × deterministic_risk + 0.6 × llm_risk
```

If the LLM is unavailable or returns invalid output, the score falls back to the deterministic risk score alone, and the run is marked degraded.

---

## Degraded mode

When `confidence < 0.5` (which includes all degraded runs), a risk adjustment is applied before threshold comparison:

```
adjusted_risk = min(1.0, overall_risk + 0.10)
```

This conservative adjustment ensures that uncertain analyses err toward more restrictive verdicts. The adjustment is capped so `adjusted_risk` never exceeds `1.0`.

The UI surface (quarantine detail page) shows a yellow "degraded analysis" banner when `is_degraded=True`.

---

## Gateway mode

When `GATEWAY_MODE=true`, the `reject` verdict becomes available. In gateway mode:

- Messages with risk > `VERDICT_THRESHOLD_QUARANTINE` that would normally get `escalate_to_admin` receive `reject` instead.
- The message is refused at the delivery layer rather than quarantined.

Gateway mode is designed for deployments where Mindwall is inline before final mail delivery (MTA-level integration), not the proxy mode used in most current deployments.

> **Current status:** Gateway mode logic is fully implemented in the verdict engine. The MTA-level integration layer (direct SMTP delivery interception before upstream delivery) is planned for Phase 11.

---

## Soft hold behavior

By default, `soft_hold` messages are **not** quarantined — they receive a `PENDING` visibility status and are tracked in `MailboxItem` but are not surfaced in the quarantine inbox.

Set `QUARANTINE_SOFT_HOLD=true` to also create `QuarantineItem` records for soft-hold verdicts, making them visible in the quarantine review queue.

---

## Policy settings (runtime overrides)

Runtime policy overrides are stored in the `policy_settings` database table. The Policy Editor UI (`/admin/policy/`) allows admins to change threshold values without redeploying.

Policy setting rows override the corresponding `Settings` value from the environment. If a key is not in the database, the environment variable value is used.

To revert a runtime override to the environment default, delete the corresponding row from `policy_settings` (currently requires direct DB access or a future admin UI action).

---

## Quarantine creation rules

| Verdict | Creates QuarantineItem? | Creates Alert? |
|---------|------------------------|----------------|
| `allow` | No | No |
| `allow_with_banner` | No | No |
| `soft_hold` | Only if `QUARANTINE_SOFT_HOLD=true` | No |
| `quarantine` | Yes | Yes |
| `escalate_to_admin` | Yes | Yes (high severity) |
| `reject` | Yes | Yes (critical severity) |
