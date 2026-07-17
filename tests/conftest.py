import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture
def dynamodb(aws):
    return boto3.resource("dynamodb", region_name="us-east-1")


@pytest.fixture
def users_table(dynamodb):
    table = dynamodb.create_table(
        TableName="users",
        KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


@pytest.fixture
def campaigns_table(dynamodb):
    table = dynamodb.create_table(
        TableName="campaigns",
        KeySchema=[{"AttributeName": "campaign_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "campaign_id", "AttributeType": "S"},
            {"AttributeName": "merchant_id", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "merchant_id-index",
                "KeySchema": [{"AttributeName": "merchant_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


@pytest.fixture
def transactions_table(dynamodb):
    table = dynamodb.create_table(
        TableName="transactions",
        KeySchema=[{"AttributeName": "idempotency_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "idempotency_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


@pytest.fixture
def processor(users_table, campaigns_table, transactions_table):
    from purchase_service import ProcessPurchase

    return ProcessPurchase(
        users_table=users_table,
        campaigns_table=campaigns_table,
        transactions_table=transactions_table,
    )
