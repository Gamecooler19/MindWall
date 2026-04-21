"""Policy settings service.

Provides a clean, audited interface for reading and persisting DB-backed
policy overrides.  Only whitelisted keys are exposed via this service.

The effective settings object returned by get_effective_settings() merges
the static environment-variable config with any live DB overrides.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service as audit_service
from app.policies.models import PolicySetting

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Whitelisted editable keys
# ---------------------------------------------------------------------------

# Maps setting key → (label, description, type, min, max)
EDITABLE_SETTINGS: dict[str, dict] = {
    "llm_enabled": {
        "label": "LLM Enabled",
        "description": "Enable Ollama LLM analysis. When false, only deterministic checks run.",
        "type": "bool",
    },
    "analysis_enabled": {
        "label": "Analysis Enabled",
        "description": (
            "Enable the full analysis pipeline. "
            "When false, messages are ingested but not analysed."
        ),
        "type": "bool",
    },
    "quarantine_soft_hold": {
        "label": "Quarantine SOFT_HOLD Verdicts",
        "description": "When enabled, SOFT_HOLD messages are also sent to quarantine for review.",
        "type": "bool",
    },
    "verdict_threshold_allow": {
        "label": "Allow Threshold",
        "description": "Risk score upper bound for ALLOW verdict (0.0-1.0).",
        "type": "float",
        "min": 0.0,
        "max": 1.0,
    },
    "verdict_threshold_allow_with_banner": {
        "label": "Allow With Banner Threshold",
        "description": "Risk score upper bound for ALLOW_WITH_BANNER verdict (0.0-1.0).",
        "type": "float",
        "min": 0.0,
        "max": 1.0,
    },
    "verdict_threshold_soft_hold": {
        "label": "Soft Hold Threshold",
        "description": "Risk score upper bound for SOFT_HOLD verdict (0.0-1.0).",
        "type": "float",
        "min": 0.0,
        "max": 1.0,
    },
    "verdict_threshold_quarantine": {
        "label": "Quarantine Threshold",
        "description": "Risk score upper bound for QUARANTINE verdict (above = ESCALATE_TO_ADMIN).",
        "type": "float",
        "min": 0.0,
        "max": 1.0,
    },
    "imap_sync_batch_size": {
        "label": "IMAP Sync Batch Size",
        "description": "Maximum number of new messages to fetch per sync run.",
        "type": "int",
        "min": 1,
        "max": 500,
    },
}


# ---------------------------------------------------------------------------
# Effective policy dataclass
# ---------------------------------------------------------------------------


@dataclass
class EffectivePolicy:
    """The merged policy: static config + DB overrides."""

    llm_enabled: bool
    analysis_enabled: bool
    quarantine_soft_hold: bool
    verdict_threshold_allow: float
    verdict_threshold_allow_with_banner: float
    verdict_threshold_soft_hold: float
    verdict_threshold_quarantine: float
    imap_sync_batch_size: int

    # Map of key -> PolicySetting for the editor UI (None = using default)
    overrides: dict[str, PolicySetting]


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


async def get_all_settings(db: AsyncSession) -> dict[str, PolicySetting]:
    """Return all persisted PolicySetting rows keyed by setting key."""
    result = await db.execute(select(PolicySetting))
    return {row.key: row for row in result.scalars().all()}


async def get_effective_policy(db: AsyncSession) -> EffectivePolicy:
    """Return the effective policy, merging env config with DB overrides."""
    from app.config import get_settings

    cfg = get_settings()
    overrides = await get_all_settings(db)

    def _get(key: str, default):  # type: ignore[no-untyped-def]
        if key in overrides:
            raw = overrides[key].value
            meta = EDITABLE_SETTINGS.get(key, {})
            t = meta.get("type", "str")
            if t == "bool":
                return raw.lower() in ("1", "true", "yes")
            if t == "float":
                return float(raw)
            if t == "int":
                return int(raw)
            return raw
        return default

    return EffectivePolicy(
        llm_enabled=_get("llm_enabled", cfg.llm_enabled),
        analysis_enabled=_get("analysis_enabled", cfg.analysis_enabled),
        quarantine_soft_hold=_get("quarantine_soft_hold", cfg.quarantine_soft_hold),
        verdict_threshold_allow=_get("verdict_threshold_allow", cfg.verdict_threshold_allow),
        verdict_threshold_allow_with_banner=_get(
            "verdict_threshold_allow_with_banner", cfg.verdict_threshold_allow_with_banner
        ),
        verdict_threshold_soft_hold=_get(
            "verdict_threshold_soft_hold", cfg.verdict_threshold_soft_hold
        ),
        verdict_threshold_quarantine=_get(
            "verdict_threshold_quarantine", cfg.verdict_threshold_quarantine
        ),
        imap_sync_batch_size=_get("imap_sync_batch_size", cfg.imap_sync_batch_size),
        overrides=overrides,
    )


async def save_setting(
    db: AsyncSession,
    *,
    key: str,
    value: str,
    actor_user_id: int | None = None,
    note: str | None = None,
) -> PolicySetting:
    """Upsert a single policy setting and record an audit event.

    Raises ValueError if ``key`` is not in EDITABLE_SETTINGS.
    """
    if key not in EDITABLE_SETTINGS:
        raise ValueError(f"Policy key {key!r} is not editable via the UI.")

    # Validate the value can be cast correctly
    _validate_value(key, value)

    result = await db.execute(select(PolicySetting).where(PolicySetting.key == key))
    row = result.scalar_one_or_none()

    old_value = row.value if row else None
    now_str = datetime.now(UTC).isoformat()

    if row is None:
        row = PolicySetting(key=key, value=value)
        db.add(row)
    else:
        row.value = value

    row.changed_by_user_id = actor_user_id
    row.changed_at = now_str
    row.note = note

    await db.flush()

    # Record audit event
    await audit_service.record_event(
        db,
        action="policy.setting_changed",
        target_type="policy_setting",
        target_id=row.id,
        actor_user_id=actor_user_id,
        from_status=old_value,
        to_status=value,
        note=note or f"Changed {key} from {old_value!r} to {value!r}",
    )

    await db.commit()
    log.info("policy.setting_changed", key=key, old=old_value, new=value, actor=actor_user_id)
    return row


def _validate_value(key: str, value: str) -> None:
    """Raise ValueError if value cannot be cast to the expected type."""
    meta = EDITABLE_SETTINGS.get(key, {})
    t = meta.get("type", "str")
    try:
        if t == "bool":
            if value.lower() not in ("0", "1", "true", "false", "yes", "no"):
                raise ValueError(f"Expected boolean-like value, got {value!r}")
        elif t == "float":
            fv = float(value)
            if "min" in meta and fv < meta["min"]:
                raise ValueError(f"{key} must be >= {meta['min']}")
            if "max" in meta and fv > meta["max"]:
                raise ValueError(f"{key} must be <= {meta['max']}")
        elif t == "int":
            iv = int(value)
            if "min" in meta and iv < meta["min"]:
                raise ValueError(f"{key} must be >= {meta['min']}")
            if "max" in meta and iv > meta["max"]:
                raise ValueError(f"{key} must be <= {meta['max']}")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid value {value!r} for {key}: {exc}") from exc
