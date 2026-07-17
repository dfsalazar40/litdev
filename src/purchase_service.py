"""Orchestrates a purchase reconciliation: resolve the winning campaign, then apply
the balance debit, cashback credit, campaign budget debit and idempotency record as
a single atomic DynamoDB transaction.

Design decisions this file implements (see CASHBACKS_DESIGN.md for the reasoning):

- No application-level locking. `main_balance >= amount` and `remaining_budget >=
  cashback` are DynamoDB ConditionExpressions evaluated atomically server-side, so
  concurrent invocations can never both succeed against a balance/budget that only
  has room for one of them (fixes the Parte 1 lost-update race condition).
- A campaign running out of `remaining_budget` mid-flight must never fail the
  purchase itself: it just falls back to the next-ranked candidate (or 0%).
- Idempotency uses a 30-minute logical window (`expires_at`), enforced with
  `attribute_not_exists(idempotency_key) OR expires_at < :now` rather than relying
  on DynamoDB's native TTL physical deletion, which is not guaranteed to happen
  promptly enough for a window this short.
- "Insufficient funds" here is a reconciliation exception (the purchase was already
  authorized upstream), not a real-time decline, so it is recorded to the ledger
  even though the main transaction rolled back nothing.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError
from mypy_boto3_dynamodb.service_resource import Table
from mypy_boto3_dynamodb.type_defs import TransactWriteItemTypeDef

import campaign_service
from errors import DuplicateRequestConflictError, InsufficientFundsError, UserNotFoundError
from models import CampaignRule, PurchaseRequest, PurchaseResult, TransactionStatus

IDEMPOTENCY_WINDOW = dt.timedelta(minutes=30)

logger = logging.getLogger(__name__)


class _CampaignBudgetExhausted(Exception):
    """Internal control-flow signal: try the next-ranked campaign candidate."""


class _DuplicateReplay(Exception):
    """Internal control-flow signal: an identical request already completed."""

    def __init__(self, result: PurchaseResult):
        super().__init__()
        self.result = result


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _same_request(existing_item: dict[str, Any], request: PurchaseRequest) -> bool:
    return (
        existing_item.get("user_id") == request.user_id
        and existing_item.get("merchant_id") == request.merchant_id
        and Decimal(str(existing_item.get("purchase_amount"))) == request.purchase_amount
    )


def _result_from_item(item: dict[str, Any]) -> PurchaseResult:
    return PurchaseResult(
        status=TransactionStatus(item["status"]),
        user_id=item["user_id"],
        idempotency_key=item["idempotency_key"],
        applied_campaign_id=item.get("applied_campaign_id"),
        cashback_rate_applied=Decimal(str(item.get("cashback_rate_applied", "0"))),
        cashback_earned=Decimal(str(item.get("cashback_earned", "0"))),
        main_balance_after=Decimal(str(item["main_balance_after"])) if item.get("main_balance_after") is not None else None,
        cashback_balance_after=Decimal(str(item["cashback_balance_after"])) if item.get("cashback_balance_after") is not None else None,
    )


class ProcessPurchase:
    def __init__(
        self,
        users_table: Table,
        campaigns_table: Table,
        transactions_table: Table,
        clock: Callable[[], dt.datetime] = _utcnow,
    ) -> None:
        self.users_table = users_table
        self.campaigns_table = campaigns_table
        self.transactions_table = transactions_table
        self._clock = clock
        self._client = users_table.meta.client

    def handle(self, request: PurchaseRequest) -> PurchaseResult:
        user = self.users_table.get_item(Key={"user_id": request.user_id}).get("Item")
        if user is None:
            raise UserNotFoundError(request.user_id)

        candidates = campaign_service.rank_candidates(
            campaign_service.fetch_candidates(self.campaigns_table, request.merchant_id),
            request.purchase_amount,
            request.purchase_timestamp,
        )
        # `None` is always the last attempt: no campaign applies, 0% cashback.
        attempts: list[CampaignRule | None] = [*candidates, None]

        for campaign in attempts:
            try:
                return self._attempt(request, campaign)
            except _CampaignBudgetExhausted:
                continue
            except _DuplicateReplay as replay:
                return replay.result
            except InsufficientFundsError:
                self._record_rejection(request)
                raise

        # Unreachable: the final attempt (campaign=None) never raises _CampaignBudgetExhausted.
        raise AssertionError("no candidate applied and no fallback available")

    def _attempt(self, request: PurchaseRequest, campaign: CampaignRule | None) -> PurchaseResult:
        cashback_rate = campaign.cashback_rate if campaign else Decimal("0")
        cashback_earned = campaign.compute_cashback(request.purchase_amount) if campaign else Decimal("0.00")

        now = self._clock()
        transact_items: list[TransactWriteItemTypeDef] = [
            {
                "Update": {
                    "TableName": self.users_table.table_name,
                    "Key": {"user_id": request.user_id},
                    "UpdateExpression": "ADD main_balance :neg_amount, cashback_balance :cashback",
                    "ConditionExpression": "main_balance >= :amount",
                    "ExpressionAttributeValues": {
                        ":neg_amount": -request.purchase_amount,
                        ":cashback": cashback_earned,
                        ":amount": request.purchase_amount,
                    },
                }
            }
        ]

        if campaign is not None and campaign.total_budget is not None:
            transact_items.append(
                {
                    "Update": {
                        "TableName": self.campaigns_table.table_name,
                        "Key": {"campaign_id": campaign.campaign_id},
                        "UpdateExpression": "ADD remaining_budget :neg_cashback",
                        "ConditionExpression": "remaining_budget >= :cashback",
                        "ExpressionAttributeValues": {
                            ":neg_cashback": -cashback_earned,
                            ":cashback": cashback_earned,
                        },
                    }
                }
            )

        transactions_index = len(transact_items)
        transact_items.append(
            {
                "Put": {
                    "TableName": self.transactions_table.table_name,
                    "Item": {
                        "idempotency_key": request.idempotency_key,
                        "user_id": request.user_id,
                        "merchant_id": request.merchant_id,
                        "purchase_amount": request.purchase_amount,
                        "applied_campaign_id": campaign.campaign_id if campaign else None,
                        "cashback_rate_applied": cashback_rate,
                        "cashback_earned": cashback_earned,
                        "status": TransactionStatus.COMPLETED.value,
                        "created_at": now.isoformat(),
                        "expires_at": int((now + IDEMPOTENCY_WINDOW).timestamp()),
                    },
                    "ConditionExpression": "attribute_not_exists(idempotency_key) OR expires_at < :now",
                    "ExpressionAttributeValues": {":now": int(now.timestamp())},
                }
            }
        )

        try:
            self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            self._handle_cancellation(exc, request, transact_items, transactions_index)

        main_balance_after, cashback_balance_after = self._read_current_balances(request.user_id)
        self._backfill_ledger_balances(request.idempotency_key, main_balance_after, cashback_balance_after)

        return PurchaseResult(
            status=TransactionStatus.COMPLETED,
            user_id=request.user_id,
            idempotency_key=request.idempotency_key,
            applied_campaign_id=campaign.campaign_id if campaign else None,
            cashback_rate_applied=cashback_rate,
            cashback_earned=cashback_earned,
            main_balance_after=main_balance_after,
            cashback_balance_after=cashback_balance_after,
        )

    def _handle_cancellation(
        self,
        exc: ClientError,
        request: PurchaseRequest,
        transact_items: list[TransactWriteItemTypeDef],
        transactions_index: int,
    ) -> None:
        reasons = exc.response["CancellationReasons"]

        if reasons[0].get("Code") == "ConditionalCheckFailed":
            raise InsufficientFundsError(request.user_id, request.purchase_amount) from exc

        campaign_included = len(transact_items) == 3
        if campaign_included and reasons[1].get("Code") == "ConditionalCheckFailed":
            raise _CampaignBudgetExhausted() from exc

        if reasons[transactions_index].get("Code") == "ConditionalCheckFailed":
            existing = self.transactions_table.get_item(
                Key={"idempotency_key": request.idempotency_key}
            ).get("Item")
            if existing is not None and _same_request(existing, request):
                raise _DuplicateReplay(_result_from_item(existing)) from exc
            if existing is not None:
                raise DuplicateRequestConflictError(request.idempotency_key) from exc

        # No recognizable condition failure (e.g. transient throttling on an
        # unrelated item): surface the original error so it is retried.
        raise exc

    def _read_current_balances(self, user_id: str) -> tuple[Decimal, Decimal]:
        item = self.users_table.get_item(
            Key={"user_id": user_id}, ConsistentRead=True
        ).get("Item", {})
        return Decimal(str(item.get("main_balance", "0"))), Decimal(str(item.get("cashback_balance", "0")))

    def _backfill_ledger_balances(
        self, idempotency_key: str, main_balance_after: Decimal, cashback_balance_after: Decimal
    ) -> None:
        # Best-effort enrichment: TransactWriteItems has no ReturnValues, so the
        # post-write balances can only be known via a follow-up read. This does not
        # affect correctness (already guaranteed by the conditional write above) -
        # it only improves the audit row's usefulness.
        try:
            self.transactions_table.update_item(
                Key={"idempotency_key": idempotency_key},
                UpdateExpression="SET main_balance_after = :b, cashback_balance_after = :c",
                ExpressionAttributeValues={":b": main_balance_after, ":c": cashback_balance_after},
            )
        except ClientError:
            logger.warning("failed to backfill ledger balances for %s", idempotency_key, exc_info=True)

    def _record_rejection(self, request: PurchaseRequest) -> None:
        # Standalone (non-transactional) audit record for a terminal rejection, so a
        # redelivered SQS message for the same purchase is recognized as already
        # handled instead of being reconsidered indefinitely.
        now = self._clock()
        try:
            self.transactions_table.put_item(
                Item={
                    "idempotency_key": request.idempotency_key,
                    "user_id": request.user_id,
                    "merchant_id": request.merchant_id,
                    "purchase_amount": request.purchase_amount,
                    "applied_campaign_id": None,
                    "cashback_rate_applied": Decimal("0"),
                    "cashback_earned": Decimal("0"),
                    "status": TransactionStatus.REJECTED_INSUFFICIENT_FUNDS.value,
                    "created_at": now.isoformat(),
                    "expires_at": int((now + IDEMPOTENCY_WINDOW).timestamp()),
                },
                ConditionExpression="attribute_not_exists(idempotency_key) OR expires_at < :now",
                ExpressionAttributeValues={":now": int(now.timestamp())},
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            # Already recorded by a previous attempt/redelivery - nothing more to do.
