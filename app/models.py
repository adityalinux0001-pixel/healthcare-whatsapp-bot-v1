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
    # Hair-loss onboarding profile. New columns are nullable to preserve every
    # existing production row during the product-domain transition.
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    hair_wash_frequency: Mapped[str | None] = mapped_column(String(40), nullable=True)
    water_hardness: Mapped[str | None] = mapped_column(String(40), nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    sugary_food_drink_intake: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Sensitive answer: stored only when explicitly collected from adults and
    # omitted from model context unless the user explicitly asks to recall it.
    sexually_active: Mapped[str | None] = mapped_column(String(30), nullable=True)
    family_hair_loss: Mapped[str | None] = mapped_column(String(20), nullable=True)
    family_hair_loss_relation: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dairy_intake: Mapped[str | None] = mapped_column(String(40), nullable=True)
    hair_onboarding_complete: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Legacy weight-loss profile columns retained for compatibility/admin/history.
    # Hair assistant prompts do not use these fields for personalization.
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    activity_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    goal: Mapped[str | None] = mapped_column(String(30), nullable=True)
    diet_preference: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Legacy health fields are retained for existing profiles and safety context.
    # The hair-specific onboarding does not treat NULL as a user-declared answer.
    allergies: Mapped[str | None] = mapped_column(Text, nullable=True)
    medical_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Legacy field retained so existing profiles and historical data are not lost.
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
    hair_care_plans: Mapped[list["HairCarePlan"]] = relationship(back_populates="user")
    payment_links: Mapped[list["PaymentLink"]] = relationship(back_populates="user")

    # Hair-focused onboarding requirements. Family relation is conditional and is
    # only required when the user answers that hair loss runs in the family.
    REQUIRED_FIELDS = [
        "age", "city", "hair_wash_frequency", "water_hardness",
        "height_cm", "weight_kg", "sugary_food_drink_intake",
        "family_hair_loss", "dairy_intake",
    ]

    def profile_dict(
        self, *, include_sensitive: bool = False, include_legacy: bool = False
    ) -> dict:
        """Return a hair-focused profile; protect sensitive/legacy values by default."""
        profile = {
            "age": self.age,
            "city": self.city,
            "height_cm": self.height_cm,
            "weight_kg": self.weight_kg,
            "hair_wash_frequency": self.hair_wash_frequency,
            "water_hardness": self.water_hardness,
            "sugary_food_drink_intake": self.sugary_food_drink_intake,
            "family_hair_loss": self.family_hair_loss,
            "family_hair_loss_relation": self.family_hair_loss_relation,
            "dairy_intake": self.dairy_intake,
        }
        # Do not turn unanswered legacy questions into a claim that the user has
        # explicitly reported no allergies/medical conditions.
        if self.allergies:
            profile["allergies"] = self.allergies
        if self.medical_conditions:
            profile["medical_conditions"] = self.medical_conditions
        if include_sensitive:
            profile["sexually_active"] = self.sexually_active
        if include_legacy:
            profile.update({
                "name": self.name,
                "gender": self.gender,
                "activity_level": self.activity_level,
                "goal": self.goal,
                "diet_preference": self.diet_preference,
                "food_dislikes": self.food_dislikes,
            })
        return profile

    def missing_fields(self) -> list[str]:
        missing = [f for f in self.REQUIRED_FIELDS if getattr(self, f) in (None, "")]
        # Do not solicit sexual-activity information from minors.
        if self.age is not None and self.age >= 18 and self.sexually_active in (None, ""):
            missing.append("sexually_active")
        # Keep family history and its optional details together in the conversation.
        if self.family_hair_loss == "yes" and self.family_hair_loss_relation in (None, ""):
            missing.append("family_hair_loss_relation")
        # Restore requested order: sexual-activity question before family history;
        # relation (if needed) is immediately after family history, before dairy.
        order = [
            "age", "city", "hair_wash_frequency", "water_hardness", "height_cm",
            "weight_kg", "sugary_food_drink_intake", "sexually_active",
            "family_hair_loss", "family_hair_loss_relation", "dairy_intake",
        ]
        return [field for field in order if field in missing]



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
    # Paid first-time subscriptions stay pending with NULL dates until the first
    # hair-care routine is successfully delivered. This prevents launch-prep days
    # from consuming the user's paid term.
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    term_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # pending, active, expired
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

    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscriptions.id"), nullable=True, index=True
    )
   
    plan_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Monotonic service-day number across renewals. It does not reset on renewal.
    day_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, default="", server_default="")

    delivery_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_send_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    last_modification_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
 
    checkin_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    checkin_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkin_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "plan_date", name="uq_diet_plan_user_date"),
        UniqueConstraint("user_id", "day_number", name="uq_diet_plan_user_day"),
        Index("ix_diet_plan_user_date", "user_id", "plan_date"),
        Index("ix_diet_plan_user_day", "user_id", "day_number"),
    )

    user: Mapped["User"] = relationship(back_populates="diet_plans")
    subscription: Mapped["Subscription | None"] = relationship()


class HairCarePlan(Base):
    """Generated daily non-medical hair-care routines, separate from legacy diet plans."""
    __tablename__ = "hair_care_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscriptions.id"), nullable=True, index=True
    )
    plan_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Lifecycle: generating, pending, sending, sent, awaiting_window, failed, unknown.
    delivery_status: Mapped[str] = mapped_column(
        String(24), default="generating", server_default="generating", nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    send_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_send_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_send_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(150), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_modification_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkin_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    checkin_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkin_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checkin_prompt_status: Mapped[str] = mapped_column(
        String(20), default="not_sent", server_default="not_sent", nullable=False
    )
    checkin_prompt_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    checkin_prompt_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "plan_date", name="uq_hair_care_plan_user_date"),
        UniqueConstraint("user_id", "day_number", name="uq_hair_care_plan_user_day"),
        Index("ix_hair_care_plan_user_date", "user_id", "plan_date"),
        Index("ix_hair_care_plan_user_day", "user_id", "day_number"),
        Index(
            "ix_hair_care_plan_checkin_retry",
            "delivery_status", "checkin_status", "checkin_prompt_status", "checkin_sent_at",
        ),
    )

    user: Mapped["User"] = relationship(back_populates="hair_care_plans")
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