"""Parses the raw purchase event (an SQS message body) into a typed PurchaseRequest.

Every field here maps directly to a bug from the Parte 1 review: the original
lambda_handler trusted event['user_id'] / event['purchase_amount'] blindly and let
KeyError / decimal.InvalidOperation bubble into a bare `except Exception: pass`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from errors import InvalidInputError
from models import PurchaseRequest

REQUIRED_FIELDS = (
    "idempotency_key",
    "user_id",
    "merchant_id",
    "purchase_amount",
    "purchase_timestamp",
)


def parse_purchase_event(raw: dict) -> PurchaseRequest:
    if not isinstance(raw, dict):
        raise InvalidInputError("event body must be a JSON object")

    missing = [field for field in REQUIRED_FIELDS if raw.get(field) in (None, "")]
    if missing:
        raise InvalidInputError(f"Missing required field(s): {', '.join(missing)}")

    idempotency_key = str(raw["idempotency_key"])
    user_id = str(raw["user_id"])
    merchant_id = str(raw["merchant_id"])

    try:
        purchase_amount = Decimal(str(raw["purchase_amount"]))
    except InvalidOperation as exc:
        raise InvalidInputError(
            "purchase_amount is not a valid decimal number", field="purchase_amount"
        ) from exc

    # Decimal("NaN") and Decimal("Infinity") both parse successfully above (no
    # InvalidOperation) - NaN then raises InvalidOperation on the comparison below
    # instead, and Infinity would sail through it. Both must be rejected explicitly.
    if not purchase_amount.is_finite():
        raise InvalidInputError("purchase_amount must be a finite number", field="purchase_amount")

    if purchase_amount <= 0:
        raise InvalidInputError("purchase_amount must be greater than zero", field="purchase_amount")

    exponent = purchase_amount.as_tuple().exponent
    assert isinstance(exponent, int)  # guaranteed by is_finite() above
    if -exponent > 2:
        raise InvalidInputError(
            "purchase_amount cannot have more than 2 decimal places", field="purchase_amount"
        )

    raw_timestamp = str(raw["purchase_timestamp"]).replace("Z", "+00:00")
    try:
        purchase_timestamp = dt.datetime.fromisoformat(raw_timestamp)
    except ValueError as exc:
        raise InvalidInputError(
            "purchase_timestamp is not valid ISO 8601", field="purchase_timestamp"
        ) from exc

    if purchase_timestamp.tzinfo is None:
        raise InvalidInputError(
            "purchase_timestamp must include a UTC timezone offset", field="purchase_timestamp"
        )

    return PurchaseRequest(
        idempotency_key=idempotency_key,
        user_id=user_id,
        merchant_id=merchant_id,
        purchase_amount=purchase_amount,
        purchase_timestamp=purchase_timestamp,
    )
