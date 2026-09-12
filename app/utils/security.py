import hmac
import hashlib


def verify_meta_signature(app_secret: str, payload: bytes, signature_header: str | None) -> bool:
    """Meta signs every webhook delivery with the app secret (HMAC-SHA256).
    Without this check, anyone who finds your webhook URL could inject fake
    messages or payment confirmations."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), payload, hashlib.sha256).hexdigest()
    received = signature_header.split("sha256=", 1)[1]
    return hmac.compare_digest(expected, received)


def verify_razorpay_signature(webhook_secret: str, payload: bytes, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    expected = hmac.new(webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def mask_identifier(value: str | None, visible_tail: int = 4) -> str:
    """Mask phone/payment-like identifiers in logs while retaining traceability."""
    if not value:
        return ""
    value = str(value)
    if len(value) <= visible_tail:
        return "*" * len(value)
    return "*" * (len(value) - visible_tail) + value[-visible_tail:]
