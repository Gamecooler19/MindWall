"""Auth routes — login, logout.

Form-based authentication using server-side sessions (Starlette SessionMiddleware).
Progressive enhancement: the login form works without JavaScript.
Error feedback is rendered server-side via template re-render.
"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import authenticate_user
from app.dependencies import get_db, get_templates

log = structlog.get_logger(__name__)

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Render the login page. Redirect to dashboard if already authenticated."""
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)  # type: ignore[return-value]
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Annotated[AsyncSession, Depends(get_db)],
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Process login form. Re-render the form with an error on failure."""
    user = await authenticate_user(db, email, password)

    if user is None:
        return templates.TemplateResponse(
            "auth/login.html",
            {
                "request": request,
                "error": "Invalid email or password.",
                "prefill_email": email,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Store minimal, non-sensitive identity data in the signed session cookie.
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["user_role"] = user.role.value

    log.info("session.created", user_id=user.id)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)  # type: ignore[return-value]


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Clear the session and redirect to the login page."""
    user_id = request.session.get("user_id")
    request.session.clear()
    log.info("session.cleared", user_id=user_id)
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
