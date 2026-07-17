"""Pure unit tests for campaign_service.rank_candidates (no AWS involved)."""

import datetime as dt
from decimal import Decimal

from campaign_service import rank_candidates
from models import CampaignRule

NOW = dt.datetime(2026, 7, 17, 12, 0, tzinfo=dt.timezone.utc)


def make_rule(campaign_id, priority, remaining_budget=None, total_budget=None, **overrides):
    defaults = dict(
        campaign_id=campaign_id,
        merchant_id="mch-starbucks",
        cashback_rate=Decimal("0.10"),
        priority=priority,
        total_budget=total_budget,
        remaining_budget=remaining_budget,
    )
    defaults.update(overrides)
    return CampaignRule(**defaults)


def test_higher_priority_wins():
    low = make_rule("low", priority=1)
    high = make_rule("high", priority=10)
    ranked = rank_candidates([low, high], Decimal("50"), NOW)
    assert [r.campaign_id for r in ranked] == ["high", "low"]


def test_tie_break_by_remaining_budget():
    less_budget = make_rule("less", priority=5, total_budget=Decimal("100"), remaining_budget=Decimal("10"))
    more_budget = make_rule("more", priority=5, total_budget=Decimal("100"), remaining_budget=Decimal("90"))
    ranked = rank_candidates([less_budget, more_budget], Decimal("50"), NOW)
    assert [r.campaign_id for r in ranked] == ["more", "less"]


def test_unlimited_budget_beats_limited_budget_on_tie():
    limited = make_rule("limited", priority=5, total_budget=Decimal("100"), remaining_budget=Decimal("99"))
    unlimited = make_rule("unlimited", priority=5, total_budget=None, remaining_budget=None)
    ranked = rank_candidates([limited, unlimited], Decimal("50"), NOW)
    assert [r.campaign_id for r in ranked] == ["unlimited", "limited"]


def test_final_tie_break_is_deterministic_by_campaign_id():
    a = make_rule("aaa", priority=5)
    b = make_rule("bbb", priority=5)
    ranked = rank_candidates([b, a], Decimal("50"), NOW)
    assert [r.campaign_id for r in ranked] == ["aaa", "bbb"]


def test_ineligible_candidates_are_filtered_out():
    exhausted = make_rule("exhausted", priority=100, total_budget=Decimal("10"), remaining_budget=Decimal("0"))
    inactive = make_rule("inactive", priority=100, status="INACTIVE")
    below_threshold = make_rule("below-threshold", priority=100, min_purchase_amount=Decimal("1000"))
    eligible = make_rule("eligible", priority=1)

    ranked = rank_candidates(
        [exhausted, inactive, below_threshold, eligible], Decimal("50"), NOW
    )
    assert [r.campaign_id for r in ranked] == ["eligible"]


def test_empty_candidates_returns_empty_list():
    assert rank_candidates([], Decimal("50"), NOW) == []
