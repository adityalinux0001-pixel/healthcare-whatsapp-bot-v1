from datetime import datetime, timedelta, timezone

from app.services.plan_revision_policy import is_duplicate_modification


def test_no_previous_instruction_is_never_a_duplicate():
    now = datetime.now(timezone.utc)
    assert not is_duplicate_modification(None, None, "add more protein", now)


def test_same_complaint_restated_shortly_after_is_a_duplicate():
    """Regression test for a real failure: the user said "I missed today's
    exercise it was a busy day!", the plan was updated, and a couple of turns
    later the user restated it as "i just only said i missed today's
    exercise!" -- a close paraphrase of the same request, minutes later. This
    should be recognized as a duplicate instead of triggering another full
    plan regeneration.
    """
    now = datetime.now(timezone.utc)
    previous = "I missed today's exercise it was a busy day!"
    restated = "i just only said i missed today's exercise!"
    assert is_duplicate_modification(previous, now - timedelta(minutes=2), restated, now)


def test_genuinely_different_instruction_is_not_a_duplicate():
    now = datetime.now(timezone.utc)
    previous = "I missed today's exercise it was a busy day!"
    new_request = "please make dinner vegan tonight, no paneer"
    assert not is_duplicate_modification(previous, now - timedelta(minutes=2), new_request, now)


def test_similar_instruction_outside_the_time_window_is_not_a_duplicate():
    now = datetime.now(timezone.utc)
    previous = "I missed today's exercise it was a busy day!"
    restated = "i just only said i missed today's exercise!"
    assert not is_duplicate_modification(previous, now - timedelta(hours=5), restated, now)


def test_empty_new_instruction_is_not_a_duplicate():
    now = datetime.now(timezone.utc)
    assert not is_duplicate_modification("add more protein", now - timedelta(minutes=1), "", now)
