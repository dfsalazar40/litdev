"""Domain errors for the cashbacks purchase-processing module."""

from __future__ import annotations

from decimal import Decimal


class CashbackError(Exception):
    """Base class for all domain errors raised while processing a purchase event."""


class InvalidInputError(CashbackError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


class UserNotFoundError(CashbackError):
    def __init__(self, user_id: str):
        super().__init__(f"User not found: {user_id}")
        self.user_id = user_id


class InsufficientFundsError(CashbackError):
    """The user's main_balance cannot cover the purchase.

    In this event-driven design the purchase was already authorized upstream by the
    card network, so this represents a reconciliation exception (internal ledger out
    of sync), not a real-time decline.
    """

    def __init__(self, user_id: str, purchase_amount: Decimal):
        super().__init__(f"Insufficient funds for user {user_id} (amount={purchase_amount})")
        self.user_id = user_id
        self.purchase_amount = purchase_amount


class DuplicateRequestConflictError(CashbackError):
    """Same idempotency_key reused with a different payload inside the active window."""

    def __init__(self, idempotency_key: str):
        super().__init__(f"idempotency_key reused with a different payload: {idempotency_key}")
        self.idempotency_key = idempotency_key
