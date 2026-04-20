"""FastAPI dependency providers.

This module centralises all reusable dependency functions so that
route handlers stay thin and dependencies are easy to override in tests.

Key dependencies exported:
  get_db          — async database session
  get_templates   — Jinja2Templates instance
  get_current_user — requires an authenticated session
  require_admin   — requires admin role
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates

from app.auth.schemas import UserContext
from app.db.session import get_db_session

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

# Expose the generator as a dependency alias so tests can override it via
# app.dependency_overrides[get_db] = override_fn.
get_db = get_db_session

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_templates: Jinja2Templates | None = None


def set_templates(templates: Jinja2Templates) -> None:
    """Register the shared Jinja2Templates instance. Called once at startup."""
    global _templates
    _templates = templates


def get_templates() -> Jinja2Templates:
    """Return the shared Jinja2Templates instance."""
    if _templates is None:
        raise RuntimeError(
            "Templates are not initialised. "
            "Ensure set_templates() is called during application startup."
        )
    return _templates


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def get_current_user(request: Request) -> UserContext:
    """Require an authenticated session.

    Reads identity from the signed session cookie.
    Raises HTTP 401 if the session is absent or expired.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in.",
        )
    return UserContext(
        user_id=int(user_id),
        email=request.session.get("user_email", ""),
        role=request.session.get("user_role", "user"),
        is_active=True,
    )


def get_session_user(request: Request) -> dict | None:
    """Extract user info dict from session for template context.

    Returns a plain dict (suitable for Jinja2 template variables) rather than
    the typed UserContext. Returns None if the user is not logged in.
    Route handlers that require authentication should use get_current_user instead.
    """
    if not request.session.get("user_id"):
        return None
    return {
        "id": request.session["user_id"],
        "email": request.session.get("user_email", ""),
        "role": request.session.get("user_role", "user"),
    }


def require_admin(
    current_user: Annotated[UserContext, Depends(get_current_user)],
) -> UserContext:
    """Require the current user to have the 'admin' role.

    Raises HTTP 403 if the user lacks the required role.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access is required.",
        )
    return current_user


def require_analyst(
    current_user: Annotated[UserContext, Depends(get_current_user)],
) -> UserContext:
    """Require the current user to be an admin or analyst."""
    if current_user.role not in {"admin", "analyst"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Analyst or administrator access is required.",
        )
    return current_user
