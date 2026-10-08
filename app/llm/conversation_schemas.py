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
    "city",
    "hair_wash_frequency",
    "water_hardness",
    "sugary_food_drink_frequency",
    "sexually_active",
    "family_hair_loss",
    "family_hair_loss_relation",
    "dairy_intake",
]

DialogueAct = Literal["request", "follow_up", "accept_offer", "acknowledgement", "correction", "other"]

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
    dialogue_act: DialogueAct = Field(
        default="request",
        description=(
            "How the CURRENT message functions in the dialogue. Use follow_up only when it depends on a prior turn; "
            "use accept_offer when the user is accepting/continuing a specific offer made in the immediately preceding assistant turn; "
            "use correction when the user is questioning, pushing back on, or disputing something the assistant just "
            "said or did in the immediately preceding turn (e.g. \"when did I ask for that?\", \"you already sent this\", "
            "\"that's not what I meant\") -- this is not a new instruction, and answering it well requires seeing that turn; "
            "use acknowledgement for a short receipt of the assistant's last message (\"ok\", \"thanks\") with no new ask; "
            "use request for a self-contained request that can be answered without prior-turn content; use other only "
            "when none of the above fit."
        ),
    )
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
        description=(
            "Only for an explicit request to change/revise the existing current-day plan. "
            "Never set this for a question about, or reaction to, a plan change that may have already happened -- "
            "that is dialogue_act=correction with intent=general_conversation, not a new modification_instruction."
        )
    )
    clarification_question: str | None = Field(
        default=None,
        description=(
            "Only for intent=CLARIFICATION, when the current request genuinely cannot be resolved from the current "
            "message and recent context. Must stay consistent with RECENT CONVERSATION: never deny, contradict, or "
            "claim no record of something the assistant already said or did in a recent turn. If the current message "
            "is questioning or reacting to a previous assistant action rather than giving a new request, that is "
            "dialogue_act=correction with intent=general_conversation -- do not use plan_modification or this field "
            "for it, since the full answer model needs real conversational context to respond correctly, not a "
            "guessed one-line question."
        ),
    )


class PlanAnswerRequest(BaseModel):
    """Application-normalized saved-plan lookup request."""

    day_number: int | None = Field(default=None, ge=1, le=3660)
    plan_date: str | None = None
    scope: PlanScope = "full"
    section: str | None = None
