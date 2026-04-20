"""Unit tests for app.policies.constants."""

from app.policies.constants import DIMENSION_LABELS, ManipulationDimension, Verdict


class TestManipulationDimension:
    def test_all_12_dimensions_defined(self):
        dimensions = list(ManipulationDimension)
        assert len(dimensions) == 12

    def test_dimension_values_are_stable_strings(self):
        expected = {
            "authority_pressure",
            "urgency_pressure",
            "scarcity",
            "fear_threat",
            "reward_lure",
            "curiosity_bait",
            "reciprocity_obligation",
            "social_proof",
            "secrecy_isolation",
            "impersonation",
            "compliance_escalation",
            "credential_or_payment_capture",
        }
        actual = {d.value for d in ManipulationDimension}
        assert actual == expected

    def test_dimension_is_str_subclass(self):
        assert isinstance(ManipulationDimension.URGENCY_PRESSURE, str)


class TestVerdict:
    def test_all_verdicts_defined(self):
        verdicts = {v.value for v in Verdict}
        assert "allow" in verdicts
        assert "quarantine" in verdicts
        assert "reject" in verdicts
        assert "escalate_to_admin" in verdicts

    def test_verdict_is_str_subclass(self):
        assert isinstance(Verdict.QUARANTINE, str)


class TestDimensionLabels:
    def test_all_dimensions_have_labels(self):
        for dim in ManipulationDimension:
            assert dim in DIMENSION_LABELS, f"Missing label for {dim}"

    def test_labels_are_non_empty_strings(self):
        for dim, label in DIMENSION_LABELS.items():
            assert isinstance(label, str) and label.strip(), f"Empty label for {dim}"
