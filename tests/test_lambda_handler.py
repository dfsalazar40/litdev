"""End-to-end tests through lambda_handler with SQS-shaped events, against moto."""

import json
from decimal import Decimal

import pytest

import lambda_function


@pytest.fixture
def sqs_env(monkeypatch, users_table, campaigns_table, transactions_table):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("USERS_TABLE", "users")
    monkeypatch.setenv("CAMPAIGNS_TABLE", "campaigns")
    monkeypatch.setenv("TRANSACTIONS_TABLE", "transactions")
    lambda_function._processor = None
    yield
    lambda_function._processor = None


def sqs_record(message_id: str, body: dict) -> dict:
    return {"messageId": message_id, "body": json.dumps(body)}


VALID_BODY = {
    "idempotency_key": "idem-lambda-1",
    "user_id": "u-1",
    "merchant_id": "mch-starbucks",
    "purchase_amount": "50.00",
    "purchase_timestamp": "2026-07-17T12:00:00Z",
}


def test_successful_message_has_no_batch_failures(sqs_env, users_table):
    users_table.put_item(
        Item={"user_id": "u-1", "email": "u1@example.com", "main_balance": Decimal("1000"), "cashback_balance": Decimal("0")}
    )
    event = {"Records": [sqs_record("m-1", VALID_BODY)]}

    response = lambda_function.lambda_handler(event, None)

    assert response["batchItemFailures"] == []


def test_business_rejection_is_not_a_batch_failure(sqs_env, users_table):
    # unknown user -> UserNotFoundError -> terminal, must not be retried by SQS
    event = {"Records": [sqs_record("m-1", VALID_BODY)]}

    response = lambda_function.lambda_handler(event, None)

    assert response["batchItemFailures"] == []


def test_invalid_json_body_is_not_a_batch_failure(sqs_env):
    event = {"Records": [{"messageId": "m-1", "body": "not-json"}]}

    response = lambda_function.lambda_handler(event, None)

    assert response["batchItemFailures"] == []


def test_partial_batch_failure_is_reported_by_message_id(sqs_env, users_table, monkeypatch):
    users_table.put_item(
        Item={"user_id": "u-1", "email": "u1@example.com", "main_balance": Decimal("1000"), "cashback_balance": Decimal("0")}
    )

    good_record = sqs_record("m-good", VALID_BODY)
    bad_record = sqs_record("m-bad", {**VALID_BODY, "idempotency_key": "idem-lambda-2"})

    processor = lambda_function._processor_instance()
    original_handle = processor.handle
    calls = {"count": 0}

    def flaky_handle(request):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("simulated DynamoDB throttling")
        return original_handle(request)

    monkeypatch.setattr(processor, "handle", flaky_handle)

    event = {"Records": [good_record, bad_record]}
    response = lambda_function.lambda_handler(event, None)

    assert response["batchItemFailures"] == [{"itemIdentifier": "m-bad"}]
