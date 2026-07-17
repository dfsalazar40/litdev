"""Concurrency tests: this is what actually proves the Parte 1 lost-update race
condition is fixed. Real threads call the same ProcessPurchase concurrently
against the moto-mocked DynamoDB backend, so the DynamoDB ConditionExpression is
genuinely exercised under concurrent writes, not simulated with mocked timing.

Note: worker threads reuse the boto3 Table objects built in the main thread
(rather than each creating its own boto3 resource) because moto's mock_aws
backend is not reliably visible to resources constructed fresh inside a
non-main thread.
"""

import datetime as dt
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from errors import InsufficientFundsError
from models import PurchaseRequest
from purchase_service import ProcessPurchase

NOW = dt.datetime(2026, 7, 17, 12, 0, tzinfo=dt.UTC)
THREAD_COUNT = 10


def _run_purchase(processor: ProcessPurchase, purchase_amount: Decimal, merchant_id: str = "mch-starbucks"):
    request = PurchaseRequest(
        idempotency_key=str(uuid.uuid4()),
        user_id="u-1",
        merchant_id=merchant_id,
        purchase_amount=purchase_amount,
        purchase_timestamp=NOW,
    )
    try:
        return processor.handle(request)
    except InsufficientFundsError as exc:
        return exc


def test_concurrent_purchases_only_one_succeeds_when_balance_is_tight(users_table, campaigns_table, processor):
    # Balance covers exactly one $60 purchase, not two.
    users_table.put_item(
        Item={"user_id": "u-1", "email": "u1@example.com", "main_balance": Decimal("60.00"), "cashback_balance": Decimal("0")}
    )

    with ThreadPoolExecutor(max_workers=THREAD_COUNT) as pool:
        results = list(pool.map(lambda _: _run_purchase(processor, Decimal("60.00")), range(THREAD_COUNT)))

    successes = [r for r in results if isinstance(r, InsufficientFundsError) is False]
    failures = [r for r in results if isinstance(r, InsufficientFundsError)]

    assert len(successes) == 1
    assert len(failures) == THREAD_COUNT - 1

    final_balance = users_table.get_item(Key={"user_id": "u-1"})["Item"]["main_balance"]
    assert final_balance == Decimal("0.00")


def test_concurrent_purchases_race_for_campaign_budget(users_table, campaigns_table, processor):
    # Plenty of main balance - only the campaign budget is the scarce resource.
    users_table.put_item(
        Item={"user_id": "u-1", "email": "u1@example.com", "main_balance": Decimal("100000.00"), "cashback_balance": Decimal("0")}
    )
    campaigns_table.put_item(
        Item={
            "campaign_id": "starbucks-weekend",
            "merchant_id": "mch-starbucks",
            "cashback_rate": Decimal("0.10"),
            "priority": 10,
            "min_purchase_amount": Decimal("0"),
            "status": "ACTIVE",
            # Exactly enough budget for 3 of the THREAD_COUNT purchases at $10 cashback each.
            "total_budget": Decimal("30.00"),
            "remaining_budget": Decimal("30.00"),
        }
    )
    campaigns_table.put_item(
        Item={
            "campaign_id": "default-base-cashback",
            "merchant_id": "GLOBAL",
            "cashback_rate": Decimal("0.05"),
            "priority": 0,
            "min_purchase_amount": Decimal("0"),
            "status": "ACTIVE",
        }
    )

    with ThreadPoolExecutor(max_workers=THREAD_COUNT) as pool:
        results = list(pool.map(lambda _: _run_purchase(processor, Decimal("100.00")), range(THREAD_COUNT)))

    won_campaign = [r for r in results if r.applied_campaign_id == "starbucks-weekend"]
    fell_back = [r for r in results if r.applied_campaign_id == "default-base-cashback"]

    assert len(won_campaign) == 3
    assert len(fell_back) == THREAD_COUNT - 3
    # No purchase failed outright - a marketing budget running out never blocks the purchase.
    assert len(results) == THREAD_COUNT

    campaign_after = campaigns_table.get_item(Key={"campaign_id": "starbucks-weekend"})["Item"]
    assert campaign_after["remaining_budget"] == Decimal("0.00")
