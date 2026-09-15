from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
from starlette.templating import Jinja2Templates

from .auth import (
    attach_session_signature,
    clear_login_failures,
    clear_session,
    get_csrf_token,
    issue_session,
    login_allowed,
    record_login_failure,
    require_admin,
    set_csrf_cookie,
    validate_csrf,
    verify_admin_credentials,
)
from app.database import get_db
from .service import get_user_detail, list_onboarded_users

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_token": token, "error": None},
    )
    set_csrf_cookie(response, token)
    return response


@router.post("/login")
async def login(
    request: Request,
    phone: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
):
    validate_csrf(request, csrf_token)
    client_key = f"{request.client.host if request.client else 'unknown'}:{phone.strip()}"

    allowed = await login_allowed(client_key)
    if not allowed:
        token = get_csrf_token(request)
        response = templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "csrf_token": token,
                "error": "Too many failed login attempts. Please try again later.",
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
        set_csrf_cookie(response, token)
        return response

    if not verify_admin_credentials(phone, password):
        await record_login_failure(client_key)
        token = get_csrf_token(request)
        response = templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": token, "error": "Invalid credentials."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
        set_csrf_cookie(response, token)
        return response

    await clear_login_failures(client_key)
    response = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    session_token = issue_session(response)
    attach_session_signature(response, session_token)
    set_csrf_cookie(response, get_csrf_token(request))
    return response


@router.post("/logout")
async def logout(request: Request, csrf_token: str = Form(...)):
    require_admin(request)
    validate_csrf(request, csrf_token)
    response = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session(response)
    return response


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    search: str | None = Query(None, max_length=100),
):
    require_admin(request)
    users, total = await list_onboarded_users(db, page=page, page_size=25, search=search)
    total_pages = max((total + 24) // 25, 1)
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "csrf_token": token,
            "users": users,
            "page": page,
            "total": total,
            "total_pages": total_pages,
            "search": search or "",
        },
    )
    set_csrf_cookie(response, token)
    return response


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(request: Request, user_id: int, db: AsyncSession = Depends(get_db)):
    require_admin(request)
    data = await get_user_detail(db, user_id)
    if data is None:
        return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    token = get_csrf_token(request)
    response = templates.TemplateResponse(
        request=request,
        name="user_detail.html",
        context={"csrf_token": token, **data},
    )
    set_csrf_cookie(response, token)
    return response
