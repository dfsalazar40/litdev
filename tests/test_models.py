"""Pure unit tests for CampaignRule: no AWS involved."""

import datetime as dt
from decimal import Decimal

from models import CampaignRule

NOW = dt.datetime(2026, 7, 17, 12, 0, tzinfo=dt.timezone.utc)


def make_rule(**overrides) -> CampaignRule:
    defaults = dict(
        campaign_id="camp-1",
        merchant_id="mch-starbucks",
        cashback_rate=Decimal("0.10"),
        priority=10,
    )
    defaults.update(overrides)
    return CampaignRule(**defaults)


def test_applies_to_within_active_window():
    rule = make_rule(
        start_datetime=NOW - dt.timedelta(hours=1),
        end_datetime=NOW + dt.timedelta(hours=1),
    )
    assert rule.applies_to(Decimal("50"), NOW) is True


def test_does_not_apply_before_start():
    rule = make_rule(start_datetime=NOW + dt.timedelta(seconds=1))
    assert rule.applies_to(Decimal("50"), NOW) is False


def test_does_not_apply_after_end():
    rule = make_rule(end_datetime=NOW - dt.timedelta(seconds=1))
    assert rule.applies_to(Decimal("50"), NOW) is False


def test_boundary_is_inclusive_on_start_and_end():
    rule = make_rule(start_datetime=NOW, end_datetime=NOW)
    assert rule.applies_to(Decimal("50"), NOW) is True


def test_inactive_status_never_applies():
    rule = make_rule(status="INACTIVE")
    assert rule.applies_to(Decimal("50"), NOW) is False


def test_min_purchase_amount_enforced():
    rule = make_rule(min_purchase_amount=Decimal("100"))
    assert rule.applies_to(Decimal("99.99"), NOW) is False
    assert rule.applies_to(Decimal("100.00"), NOW) is True


def test_exhausted_budget_does_not_apply():
    rule = make_rule(total_budget=Decimal("50"), remaining_budget=Decimal("0"))
    assert rule.applies_to(Decimal("10"), NOW) is False


def test_unlimited_budget_always_applies_regardless_of_amount():
    rule = make_rule(total_budget=None, remaining_budget=None)
    assert rule.applies_to(Decimal("1000000"), NOW) is True


def test_compute_cashback_rounds_half_up():
    rule = make_rule(cashback_rate=Decimal("0.05"))
    # 33.33 * 0.05 = 1.6665 -> rounds to 1.67, not truncated to 1.66
    assert rule.compute_cashback(Decimal("33.33")) == Decimal("1.67")


def test_compute_cashback_respects_max_cap():
    rule = make_rule(cashback_rate=Decimal("0.10"), max_cashback_amount=Decimal("5.00"))
    assert rule.compute_cashback(Decimal("1000")) == Decimal("5.00")


def test_compute_cashback_without_cap():
    rule = make_rule(cashback_rate=Decimal("0.10"), max_cashback_amount=None)
    assert rule.compute_cashback(Decimal("1000")) == Decimal("100.00")
