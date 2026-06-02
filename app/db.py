from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import asyncpg

from app.config import get_settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        settings = get_settings()
        if not settings.supabase_db_url:
            raise RuntimeError("SUPABASE_DB_URL is not configured")
        pool_kwargs: dict = {"min_size": 0, "max_size": 5}
        if "pooler.supabase.com" in settings.supabase_db_url:
            pool_kwargs["statement_cache_size"] = 0
        _pool = await asyncpg.create_pool(settings.supabase_db_url, **pool_kwargs)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_conn():
    pool = await init_pool()
    async with pool.acquire() as conn:
        yield conn


async def upsert_gmail_account(
    google_sub: str,
    email: str,
    encrypted_refresh_token: str,
) -> uuid.UUID:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            insert into gmail_accounts (google_sub, email, refresh_token)
            values ($1, $2, $3)
            on conflict (google_sub) do update
              set email = excluded.email,
                  refresh_token = excluded.refresh_token
            returning user_id
            """,
            google_sub,
            email,
            encrypted_refresh_token,
        )
        return row["user_id"]


async def get_gmail_account(user_id: uuid.UUID) -> asyncpg.Record | None:
    async with get_conn() as conn:
        return await conn.fetchrow(
            "select * from gmail_accounts where user_id = $1",
            user_id,
        )


async def get_applications(user_id: uuid.UUID) -> list[asyncpg.Record]:
    async with get_conn() as conn:
        return await conn.fetch(
            """
            select * from applications
            where user_id = $1
            order by last_updated desc
            """,
            user_id,
        )


async def get_application(user_id: uuid.UUID, app_id: uuid.UUID) -> asyncpg.Record | None:
    async with get_conn() as conn:
        return await conn.fetchrow(
            "select * from applications where id = $1 and user_id = $2",
            app_id,
            user_id,
        )


async def get_application_emails(app_id: uuid.UUID) -> list[asyncpg.Record]:
    async with get_conn() as conn:
        return await conn.fetch(
            """
            select * from emails
            where application_id = $1
            order by received_at desc nulls last
            """,
            app_id,
        )


async def email_exists(gmail_message_id: str) -> bool:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "select 1 from emails where gmail_message_id = $1",
            gmail_message_id,
        )
        return row is not None


async def upsert_application_from_email(
    user_id: uuid.UUID,
    company: str,
    role: str | None,
    detected_stage: str,
    via_referral: bool,
    email_date: datetime,
    manual_override: bool = False,
) -> uuid.UUID:
    async with get_conn() as conn:
        existing = await conn.fetchrow(
            """
            select id, current_stage::text, manual_override
            from applications
            where user_id = $1 and company = $2 and role is not distinct from $3
            """,
            user_id,
            company,
            role,
        )

        if existing:
            app_id = existing["id"]
            new_stage = detected_stage
            if not existing["manual_override"] and not manual_override:
                from app.domain.stages import furthest

                new_stage = furthest(existing["current_stage"], detected_stage)

            await conn.execute(
                """
                update applications
                set current_stage = $2::app_stage,
                    via_referral = via_referral or $3,
                    last_updated = greatest(last_updated, $4),
                    first_seen = least(first_seen, $4)
                where id = $1
                """,
                app_id,
                new_stage,
                via_referral,
                email_date,
            )
            return app_id

        row = await conn.fetchrow(
            """
            insert into applications
              (user_id, company, role, current_stage, via_referral, first_seen, last_updated)
            values ($1, $2, $3, $4::app_stage, $5, $6, $6)
            returning id
            """,
            user_id,
            company,
            role,
            detected_stage,
            via_referral,
            email_date,
        )
        return row["id"]


async def insert_email(
    application_id: uuid.UUID,
    gmail_message_id: str,
    subject: str | None,
    from_addr: str | None,
    received_at: datetime | None,
    detected_stage: str | None,
    confidence: float | None,
    needs_review: bool,
) -> None:
    async with get_conn() as conn:
        await conn.execute(
            """
            insert into emails
              (application_id, gmail_message_id, subject, from_addr, received_at,
               detected_stage, confidence, needs_review)
            values ($1, $2, $3, $4, $5, $6::app_stage, $7, $8)
            on conflict (gmail_message_id) do nothing
            """,
            application_id,
            gmail_message_id,
            subject,
            from_addr,
            received_at,
            detected_stage,
            confidence,
            needs_review,
        )


async def update_application(
    user_id: uuid.UUID,
    app_id: uuid.UUID,
    *,
    company: str,
    role: str | None,
    current_stage: str,
    via_referral: bool,
    notes: str | None,
) -> None:
    async with get_conn() as conn:
        await conn.execute(
            """
            update applications
            set company = $3,
                role = $4,
                current_stage = $5::app_stage,
                via_referral = $6,
                notes = $7,
                manual_override = true,
                last_updated = now()
            where id = $1 and user_id = $2
            """,
            app_id,
            user_id,
            company,
            role,
            current_stage,
            via_referral,
            notes,
        )


async def merge_applications(
    user_id: uuid.UUID,
    target_id: uuid.UUID,
    source_id: uuid.UUID,
) -> None:
    async with get_conn() as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                "select * from applications where id = $1 and user_id = $2",
                target_id,
                user_id,
            )
            source = await conn.fetchrow(
                "select * from applications where id = $1 and user_id = $2",
                source_id,
                user_id,
            )
            if not target or not source:
                raise ValueError("Application not found")

            from app.domain.stages import furthest

            merged_stage = furthest(target["current_stage"], source["current_stage"])

            await conn.execute(
                "update emails set application_id = $1 where application_id = $2",
                target_id,
                source_id,
            )
            await conn.execute(
                """
                update applications
                set current_stage = $2::app_stage,
                    via_referral = $3 or via_referral,
                    first_seen = least(first_seen, $4),
                    last_updated = greatest(last_updated, $5),
                    manual_override = true
                where id = $1
                """,
                target_id,
                merged_stage,
                target["via_referral"] or source["via_referral"],
                source["first_seen"],
                source["last_updated"],
            )
            await conn.execute(
                "delete from applications where id = $1 and user_id = $2",
                source_id,
                user_id,
            )


async def find_duplicate_candidates(
    user_id: uuid.UUID,
    company: str,
    exclude_id: uuid.UUID | None = None,
) -> list[asyncpg.Record]:
    async with get_conn() as conn:
        if exclude_id:
            return await conn.fetch(
                """
                select id, company, role, current_stage::text
                from applications
                where user_id = $1
                  and lower(company) = lower($2)
                  and id != $3
                order by last_updated desc
                """,
                user_id,
                company,
                exclude_id,
            )
        return await conn.fetch(
            """
            select id, company, role, current_stage::text
            from applications
            where user_id = $1 and lower(company) = lower($2)
            order by last_updated desc
            """,
            user_id,
            company,
        )
