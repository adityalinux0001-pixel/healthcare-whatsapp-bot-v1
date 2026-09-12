import base64
import hashlib
import hmac
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis

from .settings import settings


SESSION_KEY = "admin_session"
CSRF_KEY = "csrf_token"
RATE_LIMIT_PREFIX = "admin:login:failures:"
RATE_LIMIT_WINDOW_SECONDS = 15 * 60
RATE_LIMIT_MAX_FAILURES = 5


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, n_s, r_s, p_s, salt_s, expected_s = encoded_hash.split("$", 5)
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(salt_s),
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(_b64decode(expected_s)),
        )
        return hmac.compare_digest(derived, _b64decode(expected_s))
    except (ValueError, TypeError):
        return False


def normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return digits


def verify_admin_credentials(phone: str, password: str) -> bool:
    return (
        secrets.compare_digest(normalize_phone(phone), normalize_phone(settings.admin_phone))
        and verify_password(password, settings.admin_password_hash)
    )


async def get_redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


async def login_allowed(client_key: str) -> bool:
    redis = await get_redis()
    key = RATE_LIMIT_PREFIX + hashlib.sha256(client_key.encode()).hexdigest()
    try:
        count = await redis.get(key)
        return int(count or 0) < RATE_LIMIT_MAX_FAILURES
    finally:
        await redis.aclose()


async def record_login_failure(client_key: str) -> None:
    redis = await get_redis()
    key = RATE_LIMIT_PREFIX + hashlib.sha256(client_key.encode()).hexdigest()
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    finally:
        await redis.aclose()


async def clear_login_failures(client_key: str) -> None:
    redis = await get_redis()
    key = RATE_LIMIT_PREFIX + hashlib.sha256(client_key.encode()).hexdigest()
    try:
        await redis.delete(key)
    finally:
        await redis.aclose()


def issue_session(response: Any) -> str:
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        SESSION_KEY,
        token,
        max_age=settings.admin_session_max_age,
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="strict",
        path="/admin",
    )
    return token


def session_is_valid(request: Request) -> bool:
    token = request.cookies.get(SESSION_KEY)
    secret = settings.admin_session_secret.encode("utf-8")
    if not token or len(token) < 32:
        return False
    # Bind the opaque cookie to a server-side signed value kept in the request session.
    # The signed payload is stored in a second cookie to avoid any persistent admin table.
    signature = request.cookies.get("admin_session_sig")
    if not signature:
        return False
    expected = hmac.new(secret, token.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def attach_session_signature(response: Any, token: str) -> None:
    signature = hmac.new(
        settings.admin_session_secret.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    response.set_cookie(
        "admin_session_sig",
        signature,
        max_age=settings.admin_session_max_age,
        httponly=True,
        secure=settings.admin_cookie_secure,
        samesite="strict",
        path="/admin",
    )


def clear_session(response: Any) -> None:
    response.delete_cookie(SESSION_KEY, path="/admin")
    response.delete_cookie("admin_session_sig", path="/admin")


def require_admin(request: Request) -> None:
    if not session_is_valid(request):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"},
        )


def get_csrf_token(request: Request) -> str:
    token = request.cookies.get(CSRF_KEY)
    if token:
        return token
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Any, token: str) -> None:
    response.set_cookie(
        CSRF_KEY,
        token,
        max_age=settings.admin_session_max_age,
        httponly=False,
        secure=settings.admin_cookie_secure,
        samesite="strict",
        path="/admin",
    )


def validate_csrf(request: Request, form_token: str) -> None:
    cookie_token = request.cookies.get(CSRF_KEY)
    if not cookie_token or not form_token or not secrets.compare_digest(cookie_token, form_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
