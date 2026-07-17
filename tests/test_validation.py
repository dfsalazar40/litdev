"""Pure unit tests for validation.parse_purchase_event."""

from decimal import Decimal

import pytest

from errors import InvalidInputError
from validation import parse_purchase_event

VALID_EVENT = {
    "idempotency_key": "idem-1",
    "user_id": "u-123",
    "merchant_id": "mch-starbucks",
    "purchase_amount": "50.00",
    "purchase_timestamp": "2026-07-17T14:32:00Z",
}


def test_valid_event_parses():
    request = parse_purchase_event(VALID_EVENT)
    assert request.user_id == "u-123"
    assert request.purchase_amount == Decimal("50.00")
    assert request.purchase_timestamp.tzinfo is not None


@pytest.mark.parametrize("missing_field", list(VALID_EVENT.keys()))
def test_missing_required_field_raises(missing_field):
    event = {k: v for k, v in VALID_EVENT.items() if k != missing_field}
    with pytest.raises(InvalidInputError):
        parse_purchase_event(event)


def test_non_numeric_purchase_amount_raises():
    event = {**VALID_EVENT, "purchase_amount": "not-a-number"}
    with pytest.raises(InvalidInputError):
        parse_purchase_event(event)


def test_zero_purchase_amount_raises():
    event = {**VALID_EVENT, "purchase_amount": "0"}
    with pytest.raises(InvalidInputError):
        parse_purchase_event(event)


def test_negative_purchase_amount_raises():
    # This is the Parte 1 exploit: a negative amount must never be allowed to
    # increase the user's balance.
    event = {**VALID_EVENT, "purchase_amount": "-50.00"}
    with pytest.raises(InvalidInputError):
        parse_purchase_event(event)


def test_more_than_two_decimal_places_raises():
    event = {**VALID_EVENT, "purchase_amount": "50.001"}
    with pytest.raises(InvalidInputError):
        parse_purchase_event(event)


def test_timestamp_without_timezone_raises():
    event = {**VALID_EVENT, "purchase_timestamp": "2026-07-17T14:32:00"}
    with pytest.raises(InvalidInputError):
        parse_purchase_event(event)


def test_malformed_timestamp_raises():
    event = {**VALID_EVENT, "purchase_timestamp": "not-a-date"}
    with pytest.raises(InvalidInputError):
        parse_purchase_event(event)


def test_non_dict_body_raises():
    with pytest.raises(InvalidInputError):
        parse_purchase_event("not a dict")
