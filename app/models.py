from datetime import datetime

from sqlalchemy import (
    String, Integer, Float, Boolean, DateTime, ForeignKey,
    Text, func, UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)

    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    activity_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    goal: Mapped[str | None] = mapped_column(String(30), nullable=True)
    diet_preference: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # FIX: allergies + medical_conditions are now part of onboarding required flow
    # (not hard-required in DB so NULL = "none reported", but bot always asks)
    allergies: Mapped[str | None] = mapped_column(Text, nullable=True)
    medical_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FIX: dedicated structured field for food dislikes/likes — previously these
    # only lived inside conversation_summary (regenerated every 20 messages) or
    # the 8-message rolling window, so an early "I don't like paneer" could be
    # forgotten or even hallucinated over later in the chat. Now it's a durable
    # DB column, updated the same turn it's mentioned, and injected into every
    # prompt (onboarding, general Q&A, diet plan) every single time.
    food_dislikes: Mapped[str | None] = mapped_column(Text, nullable=True)

    onboarding_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    # Explicit consent for storing/processing health-related profile information.
    # Nullable keeps existing users and development flows backward-compatible.
    health_data_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Distinguish "not asked yet" from an explicit "none" answer.
    allergies_answered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    medical_conditions_answered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Conversation summary — updated periodically so old messages can be dropped
    # from Gemini context without losing long-term memory (e.g. "hates oats")
    conversation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    messages: Mapped[list["Message"]] = relationship(back_populates="user")
    diet_plans: Mapped[list["DietPlan"]] = relationship(back_populates="user")
    payment_links: Mapped[list["PaymentLink"]] = relationship(back_populates="user")

    # Health-safety questions are explicit completion requirements. An explicit
    # "none" answer satisfies the requirement just like a non-empty answer.
    REQUIRED_FIELDS = [
        "name", "age", "gender", "height_cm", "weight_kg",
        "activity_level", "goal", "diet_preference",
    ]

    def profile_dict(self) -> dict:
        return {
            "name": self.name, "age": self.age, "gender": self.gender,
            "height_cm": self.height_cm, "weight_kg": self.weight_kg,
            "activity_level": self.activity_level, "goal": self.goal,
            "diet_preference": self.diet_preference,
            "allergies": self.allergies or "None reported",
            "medical_conditions": self.medical_conditions or "None reported",
            "food_dislikes": self.food_dislikes or "None reported",
        }

    def missing_fields(self) -> list[str]:
        missing = [f for f in self.REQUIRED_FIELDS if getattr(self, f) in (None, "")]
        if not self.allergies_answered:
            missing.append("allergies")
        if not self.medical_conditions_answered:
            missing.append("medical_conditions")
        return missing


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # FIX: razorpay_payment_id unique — prevents duplicate subscriptions
    # for the same payment even if webhook retries slip through idempotency check
    razorpay_payment_id: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True
    )
    amount_inr: Mapped[float] = mapped_column(Float)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active, expired
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="subscriptions")


class PaymentLink(Base):
    """FIX: DB-backed payment link state instead of Redis-only.
    Tracks the full lifecycle: created → pending → paid / expired."""
    __tablename__ = "payment_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    razorpay_link_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    short_url: Mapped[str] = mapped_column(String(300))
    amount_inr: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, paid, expired
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    user: Mapped["User"] = relationship(back_populates="payment_links")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user, assistant
    content: Mapped[str] = mapped_column(Text)
    # FIX: whatsapp_message_id now actually stored when saving user messages
    whatsapp_message_id: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="messages")


class DietPlan(Base):
    __tablename__ = "diet_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Subscription period that entitled the user to this plan. Nullable for
    # historical rows created before this lifecycle tracking was introduced.
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscriptions.id"), nullable=True, index=True
    )
    # Canonical plan date = midnight in Asia/Kolkata converted to UTC.
    # This keeps onboarding-time and 06:00 IST cron generation on the same day key.
    plan_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Monotonic service-day number across renewals. It does not reset on renewal.
    day_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    # pending → sending → sent. failed is retryable; unknown means the external
    # provider outcome was ambiguous and must be reconciled before retrying.
    delivery_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_send_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "plan_date", name="uq_diet_plan_user_date"),
        UniqueConstraint("user_id", "day_number", name="uq_diet_plan_user_day"),
        Index("ix_diet_plan_user_date", "user_id", "plan_date"),
        Index("ix_diet_plan_user_day", "user_id", "day_number"),
    )

    user: Mapped["User"] = relationship(back_populates="diet_plans")
    subscription: Mapped["Subscription | None"] = relationship()


class ProcessedWebhookEvent(Base):
    """Idempotency guard — Meta and Razorpay both retry webhook delivery."""
    __tablename__ = "processed_webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))  # whatsapp, razorpay
    event_id: Mapped[str] = mapped_column(String(150), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source", "event_id", name="uq_processed_webhook_source_event"),
        Index("ix_processed_webhook_source_event", "source", "event_id"),
    )
