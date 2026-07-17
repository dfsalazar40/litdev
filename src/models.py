"""Plain data types for the cashbacks module. No AWS dependency, fully unit-testable."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

TWO_PLACES = Decimal("0.01")


class TransactionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    REJECTED_INSUFFICIENT_FUNDS = "REJECTED_INSUFFICIENT_FUNDS"
    REJECTED_INVALID = "REJECTED_INVALID"


@dataclass(frozen=True)
class PurchaseRequest:
    idempotency_key: str
    user_id: str
    merchant_id: str
    purchase_amount: Decimal
    purchase_timestamp: dt.datetime


@dataclass(frozen=True)
class CampaignRule:
    campaign_id: str
    merchant_id: str
    cashback_rate: Decimal
    priority: int
    min_purchase_amount: Decimal = Decimal("0")
    max_cashback_amount: Decimal | None = None
    total_budget: Decimal | None = None
    remaining_budget: Decimal | None = None
    start_datetime: dt.datetime | None = None
    end_datetime: dt.datetime | None = None
    status: str = "ACTIVE"

    def is_active_at(self, moment: dt.datetime) -> bool:
        if self.status != "ACTIVE":
            return False
        if self.start_datetime is not None and moment < self.start_datetime:
            return False
        if self.end_datetime is not None and moment > self.end_datetime:
            return False
        return True

    def applies_to(self, purchase_amount: Decimal, moment: dt.datetime) -> bool:
        if not self.is_active_at(moment):
            return False
        if purchase_amount < self.min_purchase_amount:
            return False
        if self.total_budget is not None and (self.remaining_budget or Decimal("0")) <= 0:
            return False
        return True

    def compute_cashback(self, purchase_amount: Decimal) -> Decimal:
        cashback = purchase_amount * self.cashback_rate
        if self.max_cashback_amount is not None:
            cashback = min(cashback, self.max_cashback_amount)
        return cashback.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PurchaseResult:
    status: TransactionStatus
    user_id: str
    idempotency_key: str
    applied_campaign_id: str | None
    cashback_rate_applied: Decimal
    cashback_earned: Decimal
    main_balance_after: Decimal | None
    cashback_balance_after: Decimal | None
