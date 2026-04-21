"""Unit tests for the policy settings service.

Covers:
  - EDITABLE_SETTINGS registry
  - save_setting validates and persists
  - get_effective_policy merges defaults with DB overrides
  - Invalid key raises ValueError
  - Invalid value raises ValueError
"""

from __future__ import annotations

import asyncio

import pytest
from app.policies import service as policy_service
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.fixture()
def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


# ---------------------------------------------------------------------------
# EDITABLE_SETTINGS registry
# ---------------------------------------------------------------------------


class TestEditableSettings:
    def test_all_expected_keys_present(self):
        expected = {
            "llm_enabled",
            "analysis_enabled",
            "quarantine_soft_hold",
            "verdict_threshold_allow",
            "verdict_threshold_allow_with_banner",
            "verdict_threshold_soft_hold",
            "verdict_threshold_quarantine",
            "imap_sync_batch_size",
        }
        assert expected.issubset(policy_service.EDITABLE_SETTINGS.keys())

    def test_each_key_has_label_and_type(self):
        for key, meta in policy_service.EDITABLE_SETTINGS.items():
            assert "label" in meta, f"Missing label for {key}"
            assert "type" in meta, f"Missing type for {key}"
            assert meta["type"] in ("bool", "float", "int", "str"), f"Unknown type for {key}"


# ---------------------------------------------------------------------------
# _validate_value
# ---------------------------------------------------------------------------


class TestValidateValue:
    def test_bool_true_variants_accepted(self):
        for v in ("true", "True", "1", "yes"):
            policy_service._validate_value("llm_enabled", v)  # no exception

    def test_bool_false_variants_accepted(self):
        for v in ("false", "False", "0", "no"):
            policy_service._validate_value("llm_enabled", v)  # no exception

    def test_bool_invalid_raises(self):
        with pytest.raises(ValueError):
            policy_service._validate_value("llm_enabled", "maybe")

    def test_float_in_range_accepted(self):
        policy_service._validate_value("verdict_threshold_allow", "0.25")

    def test_float_out_of_range_raises(self):
        with pytest.raises(ValueError, match="must be"):
            policy_service._validate_value("verdict_threshold_allow", "1.5")

    def test_int_in_range_accepted(self):
        policy_service._validate_value("imap_sync_batch_size", "50")

    def test_int_out_of_range_raises(self):
        with pytest.raises(ValueError, match="must be"):
            policy_service._validate_value("imap_sync_batch_size", "0")


# ---------------------------------------------------------------------------
# save_setting — DB-backed
# ---------------------------------------------------------------------------


class TestSaveSetting:
    def test_save_creates_new_row(self, factory):
        async def _run():
            async with factory() as db:
                row = await policy_service.save_setting(
                    db,
                    key="llm_enabled",
                    value="false",
                    actor_user_id=None,
                )
                return row.key, row.value

        key, value = asyncio.get_event_loop().run_until_complete(_run())
        assert key == "llm_enabled"
        assert value == "false"

    def test_save_invalid_key_raises(self, factory):
        async def _run():
            async with factory() as db:
                await policy_service.save_setting(db, key="not_a_real_key", value="x")

        with pytest.raises(ValueError, match="not editable"):
            asyncio.get_event_loop().run_until_complete(_run())

    def test_save_invalid_value_raises(self, factory):
        async def _run():
            async with factory() as db:
                await policy_service.save_setting(
                    db, key="verdict_threshold_allow", value="not_a_float"
                )

        with pytest.raises(ValueError):
            asyncio.get_event_loop().run_until_complete(_run())

    def test_save_upserts_existing(self, factory):
        async def _run():
            async with factory() as db:
                await policy_service.save_setting(db, key="analysis_enabled", value="false")
            async with factory() as db:
                await policy_service.save_setting(db, key="analysis_enabled", value="true")
            async with factory() as db:
                rows = await policy_service.get_all_settings(db)
                return rows.get("analysis_enabled")

        row = asyncio.get_event_loop().run_until_complete(_run())
        assert row is not None
        assert row.value == "true"


# ---------------------------------------------------------------------------
# get_effective_policy
# ---------------------------------------------------------------------------


class TestGetEffectivePolicy:
    def test_returns_defaults_when_no_overrides(self, factory):
        async def _run():
            async with factory() as db:
                return await policy_service.get_effective_policy(db)

        policy = asyncio.get_event_loop().run_until_complete(_run())
        # Verify the dataclass has the expected attributes
        assert hasattr(policy, "llm_enabled")
        assert hasattr(policy, "verdict_threshold_allow")
        assert isinstance(policy.verdict_threshold_allow, float)

    def test_db_override_takes_precedence(self, factory):
        async def _run():
            async with factory() as db:
                await policy_service.save_setting(
                    db, key="verdict_threshold_quarantine", value="0.99"
                )
            async with factory() as db:
                return await policy_service.get_effective_policy(db)

        policy = asyncio.get_event_loop().run_until_complete(_run())
        assert abs(policy.verdict_threshold_quarantine - 0.99) < 0.001
