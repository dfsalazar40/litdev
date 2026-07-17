"""Integration tests against a moto-mocked DynamoDB: the real transact_write_items
path, condition expressions and cancellation-reason handling all run for real here
(only the AWS backend is faked), which is what actually exercises the atomicity fix.
"""

import datetime as dt
from decimal import Decimal

import pytest

from errors import DuplicateRequestConflictError, InsufficientFundsError, UserNotFoundError
from models import PurchaseRequest, TransactionStatus
from purchase_service import ProcessPurchase

NOW = dt.datetime(2026, 7, 17, 12, 0, tzinfo=dt.UTC)


def make_request(**overrides) -> PurchaseRequest:
    defaults = dict(
        idempotency_key="idem-1",
        user_id="u-1",
        merchant_id="mch-starbucks",
        purchase_amount=Decimal("50.00"),
        purchase_timestamp=NOW,
    )
    defaults.update(overrides)
    return PurchaseRequest(**defaults)


def put_user(users_table, user_id="u-1", main_balance="1000.00", cashback_balance="0.00"):
    users_table.put_item(
        Item={
            "user_id": user_id,
            "email": f"{user_id}@example.com",
            "main_balance": Decimal(main_balance),
            "cashback_balance": Decimal(cashback_balance),
        }
    )


def put_global_default(campaigns_table):
    campaigns_table.put_item(
        Item={
            "campaign_id": "default-base-cashback",
            "merchant_id": "GLOBAL",
            "cashback_rate": Decimal("0.05"),
            "priority": 0,
            "min_purchase_amount": Decimal("100"),
            "status": "ACTIVE",
        }
    )


def put_merchant_campaign(campaigns_table, **overrides):
    item = {
        "campaign_id": "starbucks-weekend",
        "merchant_id": "mch-starbucks",
        "cashback_rate": Decimal("0.10"),
        "priority": 10,
        "min_purchase_amount": Decimal("0"),
        "status": "ACTIVE",
    }
    item.update(overrides)
    campaigns_table.put_item(Item=item)


def test_purchase_below_threshold_gets_no_cashback(users_table, campaigns_table, processor):
    put_user(users_table)
    put_global_default(campaigns_table)
    request = make_request(purchase_amount=Decimal("50.00"))

    result = processor.handle(request)

    assert result.status == TransactionStatus.COMPLETED
    assert result.applied_campaign_id is None
    assert result.cashback_earned == Decimal("0.00")
    assert result.main_balance_after == Decimal("950.00")


def test_purchase_above_threshold_uses_global_default(users_table, campaigns_table, processor):
    put_user(users_table)
    put_global_default(campaigns_table)
    request = make_request(purchase_amount=Decimal("200.00"))

    result = processor.handle(request)

    assert result.applied_campaign_id == "default-base-cashback"
    assert result.cashback_earned == Decimal("10.00")  # 5% of 200
    assert result.main_balance_after == Decimal("800.00")
    assert result.cashback_balance_after == Decimal("10.00")


def test_merchant_campaign_outranks_global_default(users_table, campaigns_table, processor):
    put_user(users_table)
    put_global_default(campaigns_table)
    put_merchant_campaign(campaigns_table, priority=10, cashback_rate=Decimal("0.20"))
    request = make_request(purchase_amount=Decimal("200.00"))

    result = processor.handle(request)

    assert result.applied_campaign_id == "starbucks-weekend"
    assert result.cashback_earned == Decimal("40.00")  # 20% of 200


def test_insufficient_funds_raises_and_is_audited(users_table, campaigns_table, transactions_table, processor):
    put_user(users_table, main_balance="10.00")
    request = make_request(purchase_amount=Decimal("50.00"))

    with pytest.raises(InsufficientFundsError):
        processor.handle(request)

    ledger_item = transactions_table.get_item(Key={"idempotency_key": "idem-1"}).get("Item")
    assert ledger_item is not None
    assert ledger_item["status"] == TransactionStatus.REJECTED_INSUFFICIENT_FUNDS.value

    # Balance must be untouched - the transaction rolled back entirely.
    user = users_table.get_item(Key={"user_id": "u-1"})["Item"]
    assert user["main_balance"] == Decimal("10.00")


def test_unknown_user_raises(users_table, campaigns_table, processor):
    request = make_request(user_id="does-not-exist")
    with pytest.raises(UserNotFoundError):
        processor.handle(request)


def test_duplicate_request_is_idempotent_replay(users_table, campaigns_table, processor):
    put_user(users_table)
    request = make_request()

    first = processor.handle(request)
    second = processor.handle(request)

    assert second == first
    # Balance must only have been debited once.
    user = users_table.get_item(Key={"user_id": "u-1"})["Item"]
    assert user["main_balance"] == Decimal("950.00")


def test_same_key_different_body_within_window_conflicts(users_table, campaigns_table, processor):
    put_user(users_table)
    processor.handle(make_request(purchase_amount=Decimal("50.00")))

    with pytest.raises(DuplicateRequestConflictError):
        processor.handle(make_request(purchase_amount=Decimal("99.00")))


def test_same_key_different_body_after_window_is_a_new_transaction(
    users_table, campaigns_table, transactions_table
):
    clock = {"now": NOW}
    processor = ProcessPurchase(
        users_table=users_table,
        campaigns_table=campaigns_table,
        transactions_table=transactions_table,
        clock=lambda: clock["now"],
    )
    put_user(users_table)

    processor.handle(make_request(purchase_amount=Decimal("50.00")))

    clock["now"] = NOW + dt.timedelta(minutes=31)
    result = processor.handle(make_request(purchase_amount=Decimal("30.00")))

    assert result.cashback_earned == Decimal("0.00")
    user = users_table.get_item(Key={"user_id": "u-1"})["Item"]
    # 1000 - 50 (first purchase) - 30 (second, reusing the same key after expiry)
    assert user["main_balance"] == Decimal("920.00")


def test_campaign_budget_exhausted_falls_back_to_next_candidate(users_table, campaigns_table, processor):
    put_user(users_table)
    put_global_default(campaigns_table)
    put_merchant_campaign(
        campaigns_table,
        priority=10,
        cashback_rate=Decimal("0.50"),
        total_budget=Decimal("5.00"),
        remaining_budget=Decimal("0.00"),  # already exhausted
    )
    request = make_request(purchase_amount=Decimal("200.00"))

    result = processor.handle(request)

    # The exhausted merchant campaign is skipped; falls back to the GLOBAL default.
    assert result.applied_campaign_id == "default-base-cashback"
    assert result.cashback_earned == Decimal("10.00")
