"""Resolves which campaign (if any) applies to a purchase.

Ranking rule (agreed in CASHBACKS_DESIGN.md §0): highest `priority` wins; ties are
broken by whichever campaign has more `remaining_budget` (unlimited campaigns count
as infinite budget for this comparison); a final tie-break on `campaign_id` keeps the
ordering deterministic for testing.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from models import CampaignRule

GLOBAL_MERCHANT_ID = "GLOBAL"
CAMPAIGNS_MERCHANT_INDEX = "merchant_id-index"


def _parse_datetime(value: str | None) -> dt.datetime | None:
    if value is None:
        return None
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def item_to_rule(item: dict[str, Any]) -> CampaignRule:
    total_budget = item.get("total_budget")
    remaining_budget = item.get("remaining_budget")
    max_cashback_amount = item.get("max_cashback_amount")
    return CampaignRule(
        campaign_id=item["campaign_id"],
        merchant_id=item["merchant_id"],
        cashback_rate=Decimal(str(item["cashback_rate"])),
        priority=int(item["priority"]),
        min_purchase_amount=Decimal(str(item.get("min_purchase_amount", "0"))),
        max_cashback_amount=Decimal(str(max_cashback_amount)) if max_cashback_amount is not None else None,
        total_budget=Decimal(str(total_budget)) if total_budget is not None else None,
        remaining_budget=Decimal(str(remaining_budget)) if remaining_budget is not None else None,
        start_datetime=_parse_datetime(item.get("start_datetime")),
        end_datetime=_parse_datetime(item.get("end_datetime")),
        status=item.get("status", "ACTIVE"),
    )


def fetch_candidates(campaigns_table, merchant_id: str) -> list[CampaignRule]:
    """Query campaigns scoped to this merchant plus the GLOBAL bucket via the GSI."""
    items: list[dict[str, Any]] = []
    merchant_keys = {merchant_id, GLOBAL_MERCHANT_ID}
    for key in merchant_keys:
        response = campaigns_table.query(
            IndexName=CAMPAIGNS_MERCHANT_INDEX,
            KeyConditionExpression="merchant_id = :m",
            ExpressionAttributeValues={":m": key},
        )
        items.extend(response.get("Items", []))
    return [item_to_rule(item) for item in items]


def rank_candidates(
    candidates: list[CampaignRule],
    purchase_amount: Decimal,
    moment: dt.datetime,
) -> list[CampaignRule]:
    """Return the campaigns that could apply right now, best candidate first."""
    eligible = [c for c in candidates if c.applies_to(purchase_amount, moment)]

    def sort_key(rule: CampaignRule) -> tuple[int, Decimal, str]:
        budget = rule.remaining_budget if rule.remaining_budget is not None else Decimal("Infinity")
        return (-rule.priority, -budget, rule.campaign_id)

    return sorted(eligible, key=sort_key)
