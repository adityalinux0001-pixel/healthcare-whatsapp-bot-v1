"""Structured Gemini response schemas used for deterministic plan formatting."""
from pydantic import BaseModel, Field



class HairCarePlanOutput(BaseModel):
    """Structured, general-purpose daily hair-care routine (not medical treatment)."""
    morning_action: str = Field(min_length=5, max_length=350)
    wash_and_scalp_care: str = Field(min_length=5, max_length=450)
    daytime_habit: str = Field(min_length=5, max_length=350)
    nourishment_habit: str = Field(min_length=5, max_length=350)
    evening_action: str = Field(min_length=5, max_length=350)
    safety_note: str = Field(min_length=10, max_length=350)
