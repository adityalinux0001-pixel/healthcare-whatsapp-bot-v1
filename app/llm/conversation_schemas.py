from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


ProfileField = Literal[
    "name",
    "age",
    "gender",
    "height_cm",
    "weight_kg",
    "activity_level",
    "goal",
    "diet_preference",
    "allergies",
    "medical_conditions",
    "food_dislikes",
]

Intent = Literal[
    "saved_plan_retrieval",
    "profile_recall",
    "profile_update",
    "plan_modification",
    "general_health",
    "general_conversation",
    "acknowledgement",
    "clarification",
]

PlanReference = Literal["today", "yesterday", "tomorrow", "day_number", "date", "none"]
PlanScope = Literal["full", "section", "none"]


class ProfileUpdateCandidate(BaseModel):
    field: ProfileField
    value: str
    evidence: str = Field(description="Brief quote or faithful span from the CURRENT user message supporting the update.")
    confidence: float = Field(ge=0.0, le=1.0)


class ConversationRoute(BaseModel):
    """Semantic routing decision. Read/write effects are executed by the application, not the model."""

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    grounding_required: bool = Field(
        description="True when the answer should use the verified health knowledge retrieval lane."
    )

    profile_fields: list[ProfileField] = Field(
        default_factory=list,
        description="Fields the user is asking to recall. Empty unless intent is profile_recall."
    )
    response_profile_fields: list[ProfileField] = Field(
        default_factory=list,
        description=(
            "Profile fields that are genuinely relevant as hidden context for answering the CURRENT user message. "
            "Select the minimum necessary fields. Do not include name unless the user explicitly asks for it."
        ),
    )
    relevant_history_indices: list[int] = Field(
        default_factory=list,
        description=(
            "0-based indices into RECENT CONVERSATION that are genuinely relevant to the current turn. "
            "Select at most 4. Do not select stale saved-plan/profile-recall payloads unless the current user explicitly refers to them."
        ),
    )
    use_long_term_memory: bool = Field(
        default=False,
        description=(
            "True only when the answer genuinely depends on durable conversational context not already represented in the saved profile."
        ),
    )

    profile_updates: list[ProfileUpdateCandidate] = Field(
        default_factory=list,
        description="Explicit profile facts stated in the CURRENT user message. Never use prior messages as evidence."
    )

    plan_reference: PlanReference = "none"
    plan_day_number: int | None = Field(default=None, ge=1, le=3660)
    plan_date: str | None = Field(default=None, description="YYYY-MM-DD only when plan_reference=date.")
    plan_scope: PlanScope = "none"
    plan_section: str | None = Field(default=None, description="Optional section such as breakfast, lunch, dinner, exercise.")

    modification_instruction: str | None = Field(
        default=None,
        description="Only for an explicit request to change/revise the existing current-day plan."
    )
    clarification_question: str | None = None


class PlanAnswerRequest(BaseModel):
    """Application-normalized saved-plan lookup request."""

    day_number: int | None = Field(default=None, ge=1, le=3660)
    plan_date: str | None = None
    scope: PlanScope = "full"
    section: str | None = None
