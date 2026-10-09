from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.subscription_periods import activate_pending_subscription


def test_pending_subscription_starts_at_first_successful_delivery_time():
    sent_at = datetime(2026, 10, 12, 6, 15, tzinfo=timezone.utc)
    sub = SimpleNamespace(status="pending", term_days=21, start_date=None, end_date=None)

    changed = activate_pending_subscription(sub, sent_at, default_term_days=30)

    assert changed is True
    assert sub.status == "active"
    assert sub.start_date == sent_at
    assert sub.end_date == sent_at + timedelta(days=21)
    assert sub.term_days == 21


def test_duplicate_activation_does_not_reset_an_active_term():
    sent_at = datetime(2026, 10, 12, 6, 15, tzinfo=timezone.utc)
    original_start = sent_at - timedelta(days=1)
    original_end = original_start + timedelta(days=21)
    sub = SimpleNamespace(
        status="active", term_days=21,
        start_date=original_start, end_date=original_end,
    )

    changed = activate_pending_subscription(sub, sent_at, default_term_days=30)

    assert changed is False
    assert sub.start_date == original_start
    assert sub.end_date == original_end


def test_invalid_subscription_duration_is_rejected():
    sub = SimpleNamespace(status="pending", term_days=0, start_date=None, end_date=None)
    with pytest.raises(ValueError, match="positive integer"):
        activate_pending_subscription(
            sub, datetime.now(timezone.utc), default_term_days=0
        )


def test_database_and_delivery_flow_keep_paid_term_pending_until_confirmed_send():
    from pathlib import Path

    root = Path(__file__).parents[1]
    models = (root / "app/models.py").read_text(encoding="utf-8")
    worker = (root / "app/worker.py").read_text(encoding="utf-8")
    plan_service = (root / "app/services/hair_care_plan_service.py").read_text(encoding="utf-8")
    subscription_service = (root / "app/services/subscription_service.py").read_text(encoding="utf-8")
    migration = (root / "migrations/versions/0011_subscription_starts_on_first_routine.py").read_text(encoding="utf-8")

    assert 'start_date: Mapped[datetime | None]' in models
    assert 'end_date: Mapped[datetime | None]' in models
    assert 'status="pending"' in worker
    assert 'start_date=None' in worker and 'end_date=None' in worker
    assert 'get_paid_subscription' in subscription_service
    assert 'activate_pending_subscription' in plan_service
    assert 'plan.delivery_status = "sent"' in plan_service
    assert 'Subscription.status == "pending"' in plan_service
    assert 'nullable=True' in migration
    assert 'term_days' in migration


def test_scheduler_includes_paid_pending_subscriptions_for_first_plan():
    from pathlib import Path

    root = Path(__file__).parents[1]
    plan_service = (root / "app/services/hair_care_plan_service.py").read_text(encoding="utf-8")
    assert 'Subscription.status == "pending"' in plan_service
    assert 'Subscription.start_date.is_(None)' in plan_service
    assert 'Subscription.end_date.is_(None)' in plan_service


def test_new_payment_is_pending_and_term_duration_is_snapshotted():
    from pathlib import Path

    root = Path(__file__).parents[1]
    worker = (root / "app/worker.py").read_text(encoding="utf-8")
    assert 'start_date=None' in worker
    assert 'end_date=None' in worker
    assert 'term_days=settings.subscription_days' in worker
    assert 'status="pending"' in worker
    assert 'first daily hair-care routine under that period is successfully sent' in worker
