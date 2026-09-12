from fastapi import APIRouter, Request, Response, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import ProcessedWebhookEvent
from app.redis_client import get_arq_pool
from app.utils.security import verify_meta_signature
from app.utils.logging_config import logger

router = APIRouter()


@router.get("/whatsapp/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == settings.whatsapp_verify_token
    ):
        return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp/webhook")
async def receive_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_meta_signature(settings.whatsapp_app_secret, raw_body, signature):
        logger.warning("whatsapp_signature_invalid")
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    pool = await get_arq_pool()

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            messages = value.get("messages") or []
            for msg in messages:
                wa_message_id = msg.get("id")
                from_number = msg.get("from")
                if not wa_message_id or not from_number:
                    continue

                exists = await db.scalar(
                    select(ProcessedWebhookEvent).where(
                        ProcessedWebhookEvent.source == "whatsapp",
                        ProcessedWebhookEvent.event_id == wa_message_id,
                    )
                )
                if exists:
                    continue

                text = (msg.get("text") or {}).get("body", "").strip()
                if not text:
                    db.add(ProcessedWebhookEvent(source="whatsapp", event_id=wa_message_id))
                    await db.commit()
                    continue

                # Queue before marking processed. If Redis is unavailable, Meta can retry.
                await pool.enqueue_job(
                    "process_incoming_message", from_number, text, wa_message_id,
                    _job_id=f"whatsapp:{wa_message_id}",
                )
                db.add(ProcessedWebhookEvent(source="whatsapp", event_id=wa_message_id))
                await db.commit()

    return Response(status_code=200)
