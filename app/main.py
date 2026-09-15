from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.utils.logging_config import configure_logging, logger
from app.whatsapp.webhook import router as whatsapp_router
from app.payments.webhook import router as razorpay_router
from app.admin.routes import router as admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.environment)
    logger.info("app_startup", environment=settings.environment)
    yield
    from app.database import engine
    from app.redis_client import redis_client, close_arq_pool
    from app.whatsapp.client import close_http_client
    from app.llm.gemini_client import close_client as close_gemini_client
    await close_http_client()
    await close_gemini_client()
    await close_arq_pool()
    await redis_client.aclose()
    await engine.dispose()
    logger.info("app_shutdown")


docs_enabled = settings.environment != "production"
app = FastAPI(
    title="AI Diet Plan WhatsApp Bot",
    lifespan=lifespan,
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
)
app.include_router(whatsapp_router, tags=["whatsapp"])
app.include_router(razorpay_router, tags=["payments"])
app.include_router(admin_router)


@app.get("/health/live")
async def liveness():
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness():
    errors = {}
    try:
        from app.database import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        errors["postgres"] = "unavailable"

    try:
        from app.redis_client import redis_client
        await redis_client.ping()
    except Exception:
        errors["redis"] = "unavailable"

    try:
        from app.knowledge.store import kb_ready
        if not kb_ready():
            errors["knowledge_base"] = "knowledge index is empty"
    except Exception:
        errors["knowledge_base"] = "unavailable"

    if settings.worker_heartbeat_required:
        try:
            from app.redis_client import redis_client
            if not await redis_client.exists(settings.worker_heartbeat_key):
                errors["worker"] = "heartbeat missing"
        except Exception:
            errors["worker"] = "unavailable"

    if errors:
        return JSONResponse(status_code=503, content={"status": "degraded", "errors": errors})
    return {"status": "ready"}


@app.get("/health")
async def health():
    return {"status": "ok"}
