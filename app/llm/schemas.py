"""Structured Gemini response schemas used for deterministic plan formatting."""
from pydantic import BaseModel, Field


class DietPlanOutput(BaseModel):
    breakfast: str = Field(
        description=(
            "Light, simple Indian breakfast item (e.g. a fruit, roasted chana, sprouts, "
            "soaked nuts, buttermilk) with practical portion guidance. This is a small "
            "bite, not a large cooked meal like paratha, cheela, or poha."
        )
    )
    lunch: str = Field(description="Indian lunch with practical portion guidance.")
    evening_snack: str = Field(description="Simple evening snack.")
    dinner: str = Field(description="Indian dinner with practical portion guidance.")
    exercise: str = Field(description="Safe, practical exercise plan for this user for today.")
    hydration_and_routine: str = Field(description="Brief hydration, sleep, or routine suggestion.")
    safety_note: str = Field(description="Short safety caveat; do not diagnose or prescribe medication.")
