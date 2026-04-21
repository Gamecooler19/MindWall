"""Dev-only script: create or verify the default admin user.

Run by the entrypoint when MINDWALL_CREATE_ADMIN=true.
Safe to run multiple times — skips creation if user already exists.

Environment variables:
    MINDWALL_ADMIN_EMAIL    (default: admin@mindwall.local)
    MINDWALL_ADMIN_PASSWORD (default: changeme-dev-only)
"""

from __future__ import annotations

import asyncio
import os
import sys


async def main() -> None:
    email = os.environ.get("MINDWALL_ADMIN_EMAIL", "admin@mindwall.local")
    password = os.environ.get("MINDWALL_ADMIN_PASSWORD", "changeme-dev-only")

    if len(password) < 8:
        print("[create_admin] ERROR: MINDWALL_ADMIN_PASSWORD must be at least 8 characters.")
        sys.exit(1)

    from app.auth.service import hash_password
    from app.config import get_settings
    from app.users.models import User, UserRole
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    settings = get_settings()

    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        existing = result.scalar_one_or_none()
        if existing is not None:
            print(f"[create_admin] Admin user '{email}' already exists (id={existing.id}).")
        else:
            user = User(
                email=email,
                hashed_password=hash_password(password),
                role=UserRole.ADMIN,
                is_active=True,
            )
            session.add(user)
            await session.commit()
            print(f"[create_admin] Created admin user '{email}'.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
