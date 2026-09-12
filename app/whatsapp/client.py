
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from app.config import settings
from app.utils.logging_config import logger

_BASE_URL = (
    f"https://graph.facebook.com/{settings.whatsapp_api_version}"
    f"/{settings.whatsapp_phone_number_id}/messages"
)
_HEADERS = {
    "Authorization": f"Bearer {settings.whatsapp_token}",
    "Content-Type": "application/json",
}

# Module-level shared client — created once, reused forever.
# Limits are generous; tune down if you hit WhatsApp rate limits.
_http_client = httpx.AsyncClient(
    timeout=10,
    limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
)


def _retryable_whatsapp_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))



async def show_typing_indicator(incoming_wa_message_id: str) -> None:

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": incoming_wa_message_id,
        "typing_indicator": {"type": "text"},
    }
    try:
        resp = await _http_client.post(_BASE_URL, headers=_HEADERS, json=payload)
        resp.raise_for_status()
    except Exception:
        # Don't let a typing-indicator hiccup stop the real reply from going out.
        pass


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), retry=retry_if_exception(_retryable_whatsapp_error))
async def send_text_message(to: str, body: str) -> dict:
    """Free-form text — works within the WhatsApp customer-service window."""
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body}}
    resp = await _http_client.post(_BASE_URL, headers=_HEADERS, json=payload)
    if resp.is_error:
        logger.error(
            "whatsapp_text_send_failed",
            status=resp.status_code,
            response=resp.text[:2000],
            phone_number_id=settings.whatsapp_phone_number_id,
            to=to[-4:],
        )
        resp.raise_for_status()
    return resp.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), retry=retry_if_exception(_retryable_whatsapp_error))
async def send_template_message(
    to: str, template_name: str, language_code: str = "en", components: list | None = None
) -> dict:
    """Pre-approved template — required outside the 24h session window."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {"name": template_name, "language": {"code": language_code}},
    }
    if components:
        payload["template"]["components"] = components
    resp = await _http_client.post(_BASE_URL, headers=_HEADERS, json=payload)
    if resp.is_error:
        logger.error(
            "whatsapp_template_send_failed",
            status=resp.status_code,
            response=resp.text[:2000],
            phone_number_id=settings.whatsapp_phone_number_id,
            template_name=template_name,
            to=to[-4:],
        )
        resp.raise_for_status()
    return resp.json()


async def close_http_client() -> None:
    if not _http_client.is_closed:
        await _http_client.aclose()
