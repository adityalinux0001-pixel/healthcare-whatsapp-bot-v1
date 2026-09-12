"""Razorpay webhook handler with source-scoped idempotency."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request, Response, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import ProcessedWebhookEvent
from app.redis_client import get_arq_pool
from app.utils.security import verify_razorpay_signature
from app.utils.logging_config import logger

router = APIRouter()


@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    if not verify_razorpay_signature(settings.razorpay_webhook_secret, raw_body, signature):
        logger.warning("razorpay_signature_invalid")
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    event = payload.get("event", "")
    event_id = request.headers.get("X-Razorpay-Event-Id") or hashlib.sha256(raw_body).hexdigest()

    exists = await db.scalar(
        select(ProcessedWebhookEvent).where(
            ProcessedWebhookEvent.source == "razorpay",
            ProcessedWebhookEvent.event_id == event_id,
        )
    )
    if exists:
        return Response(status_code=200)

    if event != "payment_link.paid":
        return Response(status_code=200)

    try:
        entity = payload["payload"]["payment_link"]["entity"]
        phone_number = (entity.get("notes") or {}).get("phone_number")
        amount_inr = entity["amount"] / 100
        payment_id = payload["payload"]["payment"]["entity"]["id"]
        link_id = entity.get("id", "")
    except (KeyError, TypeError, ZeroDivisionError):
        logger.error("razorpay_webhook_malformed", event_id=event_id)
        return Response(status_code=200)

    if not phone_number or not payment_id or not link_id:
        logger.error("razorpay_webhook_missing_required_fields", event_id=event_id)
        return Response(status_code=200)

    pool = await get_arq_pool()
    await pool.enqueue_job("process_payment_success", phone_number, amount_inr, payment_id, link_id)
    db.add(ProcessedWebhookEvent(source="razorpay", event_id=event_id))
    await db.commit()
    logger.info("razorpay_payment_queued", event_id=event_id, payment_id=payment_id)
    return Response(status_code=200)
