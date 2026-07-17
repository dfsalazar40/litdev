"""One-off migration: inserts the legacy hardcoded rule (purchase_amount > 100 -> 5%
cashback) as the GLOBAL base campaign, so behavior is unchanged the day this system
replaces the original lambda_function.py.

Usage: python scripts/seed_campaigns.py [--table campaigns]
"""

from __future__ import annotations

import argparse
from decimal import Decimal

import boto3

DEFAULT_CAMPAIGN = {
    "campaign_id": "default-base-cashback",
    "name": "Base cashback (regla legacy migrada)",
    "merchant_id": "GLOBAL",
    "cashback_rate": Decimal("0.05"),
    "priority": 0,
    "min_purchase_amount": Decimal("100"),
    "status": "ACTIVE",
    # No total_budget/remaining_budget: this campaign is intentionally unlimited.
}


def seed(table_name: str) -> None:
    table = boto3.resource("dynamodb").Table(table_name)
    table.put_item(
        Item=DEFAULT_CAMPAIGN,
        ConditionExpression="attribute_not_exists(campaign_id)",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", default="campaigns")
    args = parser.parse_args()
    seed(args.table)
    print(f"Seeded {DEFAULT_CAMPAIGN['campaign_id']} into {args.table}")


if __name__ == "__main__":
    main()
