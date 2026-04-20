"""First-class policy constants for Mindwall.

These identifiers are stable across the entire system.
Use these enums everywhere — do not duplicate string literals.
"""

import enum


class ManipulationDimension(enum.StrEnum):
    """The 12 psychological manipulation dimensions scored by the analysis engine.

    Each dimension maps to a score in [0.0, 1.0].
    Identifiers are stable and used in:
      - LLM prompt schemas
      - database verdict storage
      - UI labels
      - policy threshold configuration
    """

    AUTHORITY_PRESSURE = "authority_pressure"
    URGENCY_PRESSURE = "urgency_pressure"
    SCARCITY = "scarcity"
    FEAR_THREAT = "fear_threat"
    REWARD_LURE = "reward_lure"
    CURIOSITY_BAIT = "curiosity_bait"
    RECIPROCITY_OBLIGATION = "reciprocity_obligation"
    SOCIAL_PROOF = "social_proof"
    SECRECY_ISOLATION = "secrecy_isolation"
    IMPERSONATION = "impersonation"
    COMPLIANCE_ESCALATION = "compliance_escalation"
    CREDENTIAL_OR_PAYMENT_CAPTURE = "credential_or_payment_capture"


class Verdict(enum.StrEnum):
    """Final policy verdict for a message.

    Used by the policy engine, quarantine service, and IMAP proxy.
    """

    ALLOW = "allow"
    ALLOW_WITH_BANNER = "allow_with_banner"
    SOFT_HOLD = "soft_hold"
    QUARANTINE = "quarantine"
    REJECT = "reject"                    # Gateway mode only
    ESCALATE_TO_ADMIN = "escalate_to_admin"


# Human-readable labels for the UI — keyed by ManipulationDimension value.
DIMENSION_LABELS: dict[str, str] = {
    ManipulationDimension.AUTHORITY_PRESSURE: "Authority Pressure",
    ManipulationDimension.URGENCY_PRESSURE: "Urgency Pressure",
    ManipulationDimension.SCARCITY: "Scarcity",
    ManipulationDimension.FEAR_THREAT: "Fear / Threat",
    ManipulationDimension.REWARD_LURE: "Reward / Lure",
    ManipulationDimension.CURIOSITY_BAIT: "Curiosity Bait",
    ManipulationDimension.RECIPROCITY_OBLIGATION: "Reciprocity / Obligation",
    ManipulationDimension.SOCIAL_PROOF: "Social Proof",
    ManipulationDimension.SECRECY_ISOLATION: "Secrecy / Isolation",
    ManipulationDimension.IMPERSONATION: "Impersonation",
    ManipulationDimension.COMPLIANCE_ESCALATION: "Compliance Escalation",
    ManipulationDimension.CREDENTIAL_OR_PAYMENT_CAPTURE: "Credential / Payment Capture",
}
