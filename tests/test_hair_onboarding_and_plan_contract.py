"""Regression contracts for the manager-approved hair onboarding and plan lifecycle."""
from pathlib import Path

from app.services.onboarding_extract import QUESTIONS

ROOT = Path(__file__).parents[1]


def test_manager_question_wording_and_options_are_preserved():
    assert "How old are you, and where do you currently live?" in QUESTIONS["age"]
    assert "Age: ___" in QUESTIONS["age"] and "City: ___" in QUESTIONS["age"]

    assert "How often do you wash your hair?" in QUESTIONS["hair_wash_frequency"]
    for option in ("Daily", "2–3 times a week", "Once a week", "Less than once a week"):
        assert option in QUESTIONS["hair_wash_frequency"]

    assert "What type of water do you usually use to wash your hair?" in QUESTIONS["water_hardness"]
    for option in ("Soft water", "Moderately hard water", "Hard water", "Very hard water", "I'm not sure"):
        assert option in QUESTIONS["water_hardness"]

    assert "What are your current height and weight?" in QUESTIONS["height_cm"]
    assert "Height: ___ cm / ft & inches" in QUESTIONS["height_cm"]
    assert "Weight: ___ kg / lbs" in QUESTIONS["height_cm"]

    assert "How much sugary food or drinks do you normally consume?" in QUESTIONS["sugary_food_drink_intake"]
    for option in ("None or very little", "Low", "Moderate", "High", "Very high"):
        assert option in QUESTIONS["sugary_food_drink_intake"]
    assert "sweets, desserts, sugary tea/coffee, soft drinks, packaged juices, etc." in QUESTIONS["sugary_food_drink_intake"]

    assert "Are you currently sexually active?" in QUESTIONS["sexually_active"]
    for option in ("Yes", "No", "Prefer not to say"):
        assert option in QUESTIONS["sexually_active"]

    assert "Does hair loss run in your family history ?" in QUESTIONS["family_hair_loss"]
    for option in ("Yes", "No", "Not sure"):
        assert option in QUESTIONS["family_hair_loss"]
    assert "Who in your family has experienced noticeable hair loss?" in QUESTIONS["family_hair_loss_relation"]
    for option in ("Father", "Mother", "Brother/Sister", "Grandparent", "Multiple family members", "Other"):
        assert option in QUESTIONS["family_hair_loss_relation"]

    assert "How much dairy do you normally consume?" in QUESTIONS["dairy_intake"]
    for option in ("None", "Low — occasionally", "Moderate — once a day", "High — 2–3 times a day", "Very high — more than 3 times a day"):
        assert option in QUESTIONS["dairy_intake"]
    assert "milk, curd/yogurt, paneer, cheese, butter, cream, etc." in QUESTIONS["dairy_intake"]


def test_daily_hair_plan_has_its_own_table_and_preserves_historical_diet_rows():
    models = (ROOT / "app/models.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations/versions/0010_hair_care_plans.py").read_text(encoding="utf-8")
    assert 'class HairCarePlan(Base)' in models
    assert '__tablename__ = "hair_care_plans"' in models
    assert 'UniqueConstraint("user_id", "plan_date"' in models
    assert 'UniqueConstraint("user_id", "day_number"' in models
    assert 'op.create_table(\n        "hair_care_plans"' in migration
    assert 'op.drop_table("hair_care_plans")' in migration
    assert 'op.drop_table("diet_plans")' not in migration


def test_daily_hair_plan_schedule_and_safe_legacy_job_compatibility():
    worker = (ROOT / "app/worker.py").read_text(encoding="utf-8")
    service = (ROOT / "app/services/hair_care_plan_service.py").read_text(encoding="utf-8")
    assert 'cron(daily_hair_care_plan_job, hour=6, minute=0)' in worker
    assert 'cron(retry_hair_care_checkins_job, minute=set(range(0, 60, 15)))' in worker
    assert 'awaiting_window' in service
    assert 'Ambiguous WhatsApp delivery result' in service
    assert 'legacy_diet_plan_job_skipped_hair_assistant' in worker
    assert 'legacy_daily_diet_plan_scheduler_disabled' in worker
    assert 'cron(daily_diet_plan_job' not in worker
    assert 'if settings.hair_care_launch_hold' in worker
    assert 'if settings.hair_care_launch_hold' in service
    assert 'CHECKIN_NO_RESPONSE = "no_response"' in service
    assert '_expire_prior_day_checkin' in service


def test_hair_care_plan_prompt_is_non_medical_and_context_grounded():
    prompts = (ROOT / "app/llm/prompts.py").read_text(encoding="utf-8")
    gemini = (ROOT / "app/llm/gemini_client.py").read_text(encoding="utf-8")
    assert "not a diagnosis, medical treatment plan, or promise of regrowth" in prompts.casefold()
    assert "Do not recommend or name medicines" in prompts
    assert "KnowledgeBaseNotReady" in gemini
    assert "_hair_plan_validation_problems" in gemini


def test_daily_routine_keeps_the_original_supported_age_range_and_skips_medication_content():
    service = (ROOT / "app/services/hair_care_plan_service.py").read_text(encoding="utf-8")
    gemini = (ROOT / "app/llm/gemini_client.py").read_text(encoding="utf-8")
    prompt = (ROOT / "app/llm/prompts.py").read_text(encoding="utf-8")
    assert "MIN_ROUTINE_AGE = 12" in service
    assert "MAX_ROUTINE_AGE = 75" in service
    assert "is_routine_eligible_age" in service
    assert "medication, treatment, lab-test, supplement" in gemini
    assert "For users aged 12–17" in prompt


def test_daily_routine_retains_high_risk_profile_gate_for_existing_users():
    service = (ROOT / "app/services/hair_care_plan_service.py").read_text(encoding="utf-8")
    assert "detect_high_risk_profile" in service
    assert "routine_eligibility_block_reason" in service
    assert "is_routine_eligible_user(user)" in service
    assert '"high_risk_profile"' in service


def test_admin_can_inspect_hair_plan_delivery_but_not_sensitive_answer():
    admin = (ROOT / "app/admin/service.py").read_text(encoding="utf-8")
    template = (ROOT / "app/admin/templates/user_detail.html").read_text(encoding="utf-8")
    assert "FROM hair_care_plans" in admin
    for status in ("delivery_status", "last_send_error", "checkin_prompt_status", "checkin_prompt_last_error"):
        assert status in admin and status in template
    user_detail_query = admin.split("user_query = text(", 1)[1].split("user_row =", 1)[0]
    assert "sexually_active" not in user_detail_query
