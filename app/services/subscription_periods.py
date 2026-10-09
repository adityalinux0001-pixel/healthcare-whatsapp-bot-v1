"""Pure subscription-period transitions, separate from database/provider side effects."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def activate_pending_subscription(
    subscription: Any,
    started_at: datetime,
    *,
    default_term_days: int,
) -> bool:
    """Start a paid pending term once, using the successful first-send time.

    Returns False for non-pending records so duplicate/late send callbacks cannot
    reset an already-running subscription clock.
    """
    if getattr(subscription, "status", None) != "pending":
        return False

    term_days = getattr(subscription, "term_days", None)
    if term_days is None:
        term_days = default_term_days
    if term_days <= 0:
        raise ValueError("Subscription term_days must be a positive integer")

    subscription.start_date = started_at
    subscription.end_date = started_at + timedelta(days=term_days)
    subscription.term_days = term_days
    subscription.status = "active"
    return True
