"""
db.py — Подключение и работа с PostgreSQL.
Используется asyncpg для асинхронных запросов.
"""

import asyncpg
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mc_user:mc_pass@postgres:5432/mc_hosting")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def init_db():
    """Создаёт таблицы, если они ещё не существуют."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username    TEXT,
                created_at  TIMESTAMP DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id           SERIAL PRIMARY KEY,
                user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
                container_id TEXT,
                ip           TEXT,
                port         INTEGER,
                ram          TEXT,
                version      TEXT,
                status       TEXT DEFAULT 'creating',
                created_at   TIMESTAMP DEFAULT NOW()
            );
        """)
    logger.info("Database tables initialised")


# ── Users ──────────────────────────────────────────────────────────────────────

async def upsert_user(telegram_id: int, username: str | None) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO users (telegram_id, username)
            VALUES ($1, $2)
            ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username
            RETURNING *
        """, telegram_id, username)
    return dict(row)


async def get_user_by_telegram_id(telegram_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1", telegram_id
        )
    return dict(row) if row else None


# ── Servers ────────────────────────────────────────────────────────────────────

async def create_server(user_id: int, ram: str, version: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO servers (user_id, ram, version, status)
            VALUES ($1, $2, $3, 'creating')
            RETURNING *
        """, user_id, ram, version)
    return dict(row)


async def update_server(server_id: int, **kwargs) -> dict | None:
    if not kwargs:
        return None
    pool = await get_pool()
    # Build dynamic SET clause
    fields = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(kwargs))
    values = list(kwargs.values())
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE servers SET {fields} WHERE id = $1 RETURNING *",
            server_id, *values
        )
    return dict(row) if row else None


async def get_server(server_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM servers WHERE id = $1", server_id)
    return dict(row) if row else None


async def get_servers_by_user(user_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM servers WHERE user_id = $1 ORDER BY created_at DESC", user_id
        )
    return [dict(r) for r in rows]


async def delete_server(server_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM servers WHERE id = $1", server_id)


async def get_next_port() -> int:
    """Возвращает следующий свободный порт, начиная с 25565."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT MAX(port) AS max_port FROM servers WHERE port IS NOT NULL"
        )
    max_port = row["max_port"] if row and row["max_port"] else 25564
    return max_port + 1
