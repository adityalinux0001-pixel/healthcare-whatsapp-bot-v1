"""Send today's already-saved diet plan through the approved WhatsApp template.

This is a test-only utility. It does not change DietPlan delivery_status or any
production scheduling state.

Usage:
    python scripts/send_daily_plan_template_test.py --user-id 2
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import DietPlan, User
from app.services.diet_plan_service import _template_plan_content
from app.whatsapp.client import send_template_message

IST = ZoneInfo("Asia/Kolkata")


def today_plan_date_utc():
    local_date = datetime.now(IST).date()
    return datetime.combine(
        local_date,
        time.min,
        tzinfo=IST,
    ).astimezone(timezone.utc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test today's daily-plan WhatsApp template."
    )
    parser.add_argument("--user-id", type=int, required=True)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    template_name = settings.whatsapp_daily_plan_template_name.strip()

    if not template_name:
        raise RuntimeError(
            "WHATSAPP_DAILY_PLAN_TEMPLATE_NAME is not configured"
        )

    async with AsyncSessionLocal() as db:
        user = await db.get(User, args.user_id)

        if not user:
            raise RuntimeError(
                f"User {args.user_id} not found"
            )

        plan = await db.scalar(
            select(DietPlan).where(
                DietPlan.user_id == user.id,
                DietPlan.plan_date == today_plan_date_utc(),
            )
        )

        if not plan or not plan.content:
            raise RuntimeError(
                f"No saved plan found for today for user {args.user_id}"
            )

        response = await send_template_message(
            user.phone_number,
            template_name,
            settings.whatsapp_daily_plan_template_language_code,
            components=[
                {
                    "type": "body",
                    "parameters": [
                        {
                            "type": "text",
                            "text": str(plan.day_number),
                        },
                        {
                            "type": "text",
                            "text": _template_plan_content(plan.content),
                        },
                    ],
                }
            ],
        )

    message_id = response.get("messages", [{}])[0].get("id")

    print(
        f"Template test sent: user_id={args.user_id}, "
        f"day={plan.day_number}, "
        f"provider_message_id={message_id or 'unknown'}"
    )


if __name__ == "__main__":
    asyncio.run(main())