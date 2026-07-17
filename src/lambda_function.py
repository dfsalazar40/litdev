"""Entry point. Triggered by SQS (purchase-events), NOT by the purchase itself -
the purchase was already authorized upstream by the card network; this Lambda
reacts to that event to reconcile the internal ledger. See CASHBACKS_DESIGN.md.

Business exceptions (bad input, unknown user, insufficient funds, a conflicting
idempotency key) are terminal outcomes: they are logged and the record is treated
as processed, so SQS does not retry something that will never succeed. Anything
else (AWS throttling, an unhandled bug) is reported as a batch item failure so the
queue's native redrive policy retries it and eventually routes it to the DLQ.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

import validation
from errors import (
    DuplicateRequestConflictError,
    InsufficientFundsError,
    InvalidInputError,
    UserNotFoundError,
)
from logging_utils import configure_logging
from purchase_service import ProcessPurchase

configure_logging()
logger = logging.getLogger(__name__)

_NON_RETRYABLE_EXCEPTIONS = (
    InvalidInputError,
    UserNotFoundError,
    InsufficientFundsError,
    DuplicateRequestConflictError,
)


def _build_processor() -> ProcessPurchase:
    dynamodb = boto3.resource("dynamodb")
    return ProcessPurchase(
        users_table=dynamodb.Table(os.environ["USERS_TABLE"]),
        campaigns_table=dynamodb.Table(os.environ["CAMPAIGNS_TABLE"]),
        transactions_table=dynamodb.Table(os.environ["TRANSACTIONS_TABLE"]),
    )


_processor: ProcessPurchase | None = None


def _processor_instance() -> ProcessPurchase:
    # Built lazily (not at import time) so unit tests can import this module
    # without AWS credentials/tables configured.
    global _processor
    if _processor is None:
        _processor = _build_processor()
    return _processor


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            try:
                body = json.loads(record["body"])
            except json.JSONDecodeError as exc:
                raise InvalidInputError(f"message body is not valid JSON: {exc}") from exc
            request = validation.parse_purchase_event(body)
            result = _processor_instance().handle(request)
            logger.info(
                "purchase processed",
                extra={
                    "message_id": message_id,
                    "idempotency_key": result.idempotency_key,
                    "user_id": result.user_id,
                    "status": result.status.value,
                    "applied_campaign_id": result.applied_campaign_id,
                    "cashback_earned": result.cashback_earned,
                },
            )
        except _NON_RETRYABLE_EXCEPTIONS as exc:
            logger.warning(
                "purchase rejected",
                extra={
                    "message_id": message_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
        except Exception:
            logger.exception(
                "unexpected error processing purchase",
                extra={"message_id": message_id},
            )
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}
