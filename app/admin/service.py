from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_onboarded_users(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    search: str | None,
) -> tuple[list[dict], int]:
    page = max(page, 1)
    page_size = min(max(page_size, 10), 100)
    offset = (page - 1) * page_size
    search_value = (search or "").strip()

    where = "WHERE u.hair_onboarding_complete = TRUE"
    params: dict[str, object] = {"limit": page_size, "offset": offset}
    if search_value:
        where += " AND (u.name ILIKE :search OR u.phone_number ILIKE :search)"
        params["search"] = f"%{search_value}%"

    count_sql = text(f"SELECT COUNT(*) FROM users u {where}")
    result = await db.execute(count_sql, params)
    total = int(result.scalar_one())

    query = text(
        f"""
        SELECT
            u.id,
            u.phone_number,
            u.name,
            u.age,
            u.city,
            u.height_cm,
            u.weight_kg,
            u.hair_wash_frequency,
            u.water_hardness,
            u.sugary_food_drink_intake,
            u.family_hair_loss,
            u.family_hair_loss_relation,
            u.dairy_intake,
            u.hair_onboarding_complete,
            u.allergies,
            u.medical_conditions,
            u.onboarding_complete,
            u.created_at,
            u.updated_at,
            EXISTS (
                SELECT 1 FROM subscriptions s
                WHERE s.user_id = u.id
                  AND s.status = 'active'
                  AND s.start_date <= NOW()
                  AND s.end_date > NOW()
            ) AS subscription_active,
            CASE
                WHEN EXISTS (
                    SELECT 1 FROM subscriptions s
                    WHERE s.user_id = u.id
                      AND s.status = 'active'
                      AND s.start_date <= NOW()
                      AND s.end_date > NOW()
                ) THEN 'active'
                WHEN EXISTS (
                    SELECT 1 FROM subscriptions s
                    WHERE s.user_id = u.id
                      AND s.status = 'pending'
                      AND s.razorpay_payment_id IS NOT NULL
                ) THEN 'paid_pending_start'
                ELSE 'inactive'
            END AS subscription_state
        FROM users u
        {where}
        ORDER BY u.created_at DESC, u.id DESC
        LIMIT :limit OFFSET :offset
        """
    )
    rows = (await db.execute(query, params)).mappings().all()
    return [dict(row) for row in rows], total


async def get_user_detail(db: AsyncSession, user_id: int) -> dict | None:
    user_query = text(
        """
        SELECT
            id, phone_number, name, age, city, height_cm, weight_kg,
            hair_wash_frequency, water_hardness, sugary_food_drink_intake,
            family_hair_loss, family_hair_loss_relation, dairy_intake,
            hair_onboarding_complete, allergies, medical_conditions, food_dislikes,
            gender, activity_level, goal, diet_preference, onboarding_complete,
            health_data_consent_at, allergies_answered, medical_conditions_answered,
            conversation_summary, created_at, updated_at
        FROM users
        WHERE id = :user_id AND hair_onboarding_complete = TRUE
        """
    )
    user_row = (await db.execute(user_query, {"user_id": user_id})).mappings().first()
    if not user_row:
        return None

    subscriptions = (
        await db.execute(
            text(
                """
                SELECT id, amount_inr, start_date, end_date, status,
                       razorpay_payment_id, created_at
                FROM subscriptions
                WHERE user_id = :user_id
                ORDER BY created_at DESC, id DESC
                """
            ),
            {"user_id": user_id},
        )
    ).mappings().all()

    hair_care_plans = (
        await db.execute(
            text(
                """
                SELECT id, plan_date, day_number, delivery_status,
                       send_attempts, last_send_attempt_at, last_send_error,
                       sent_at, provider_message_id, checkin_status,
                       checkin_prompt_status, checkin_prompt_attempts,
                       checkin_prompt_last_error, content, created_at
                FROM hair_care_plans
                WHERE user_id = :user_id
                ORDER BY plan_date DESC, id DESC
                LIMIT 30
                """
            ),
            {"user_id": user_id},
        )
    ).mappings().all()

    # Old plans remain available for audit; never delete them as part of the product switch.
    diet_plans = (
        await db.execute(
            text(
                """
                SELECT id, plan_date, day_number, delivery_status,
                       send_attempts, sent_at, provider_message_id,
                       content, created_at
                FROM diet_plans
                WHERE user_id = :user_id
                ORDER BY plan_date DESC, id DESC
                LIMIT 30
                """
            ),
            {"user_id": user_id},
        )
    ).mappings().all()

    return {
        "user": dict(user_row),
        "subscriptions": [dict(row) for row in subscriptions],
        "hair_care_plans": [dict(row) for row in hair_care_plans],
        "diet_plans": [dict(row) for row in diet_plans],
    }
