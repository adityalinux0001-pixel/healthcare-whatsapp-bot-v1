import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_hair_guidance_dataset_has_topics_and_official_reference_urls():
    data = json.loads((ROOT / "data/sources/external/hair_loss_guidelines.json").read_text(encoding="utf-8"))
    assert data["source_name"] == "Hair and Scalp Health Guidance Summaries"
    topics = {item["topic"] for item in data["sources"]}
    assert {"common_causes_and_shedding", "gentle_hair_and_scalp_care", "nutrition_and_supplements", "when_to_seek_professional_assessment"} <= topics
    urls = {url for item in data["sources"] for url in item["source_urls"]}
    assert any("aad.org" in url for url in urls)
    assert any("nhs.uk" in url for url in urls)


def test_hair_profile_migration_is_additive_and_does_not_create_a_new_table():
    migration = (ROOT / "migrations/versions/0009_hair_loss_onboarding_profile.py").read_text(encoding="utf-8")
    assert 'down_revision = "0008"' in migration
    assert 'op.add_column("users"' in migration
    assert "op.create_table(" not in migration
    assert 'sa.Column("hair_onboarding_complete", sa.Boolean(), nullable=False' in migration
    assert 'sa.Column("sexually_active", sa.String(length=30), nullable=True)' in migration


def test_legacy_diet_plan_delivery_is_not_scheduled_and_old_jobs_are_noops():
    worker = (ROOT / "app/worker.py").read_text(encoding="utf-8")
    assert 'cron(daily_diet_plan_job' not in worker
    assert 'cron(generate_and_send_daily_plans' not in worker
    assert 'legacy_diet_plan_job_skipped_hair_assistant' in worker
    assert 'legacy_daily_diet_plan_scheduler_disabled' in worker
    assert 'daily_diet_plan_job,           # compatibility' in worker


def test_launch_hold_is_enabled_by_default_and_guards_all_plan_paths():
    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    worker = (ROOT / "app/worker.py").read_text(encoding="utf-8")
    conversation = (ROOT / "app/services/conversation_service.py").read_text(encoding="utf-8")
    plan_service = (ROOT / "app/services/hair_care_plan_service.py").read_text(encoding="utf-8")
    launch = (ROOT / "app/services/launch_mode.py").read_text(encoding="utf-8")
    assert "hair_care_launch_hold: bool = True" in config
    assert "if settings.hair_care_launch_hold" in worker
    assert "if settings.hair_care_launch_hold" in conversation
    assert "if settings.hair_care_launch_hold" in plan_service
    assert "hair_care_launch_hold_direct_generation_skipped" in plan_service
    assert "hair_care_launch_hold_direct_delivery_skipped" in plan_service
    assert "2–3 business days" in launch
    assert "hair/scalp questions and personalized guidance are not available yet" in launch
    assert "HAIR_CARE_LAUNCH_HOLD_STANDBY_REPLY" in launch


def test_profile_context_and_standard_chat_do_not_use_sexual_activity():
    context = (ROOT / "app/llm/context.py").read_text(encoding="utf-8")
    prompt = (ROOT / "app/llm/prompts.py").read_text(encoding="utf-8")
    assert 'selected_fields.discard("sexually_active")' in context
    assert "Never include the sexual-activity field in response context" in prompt


def test_sexual_activity_is_only_in_profile_when_explicitly_requested():
    from app.models import User

    user = User(phone_number="test", age=30, city="Indore", sexually_active="yes")
    assert "sexually_active" not in user.profile_dict()
    assert user.profile_dict(include_sensitive=True)["sexually_active"] == "yes"
