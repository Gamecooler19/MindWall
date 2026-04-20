"""Pydantic schemas for the auth domain.

These are used for request validation, response serialisation,
and the authenticated user context passed through dependencies.
"""

from pydantic import BaseModel


class UserContext(BaseModel):
    """Authenticated user context attached to each request via session.

    Populated by the get_current_user dependency from the signed session cookie.
    """

    user_id: int
    email: str
    role: str
    is_active: bool
