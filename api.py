"""
api.py — FastAPI-сервис для управления Minecraft-серверами.

Бот обращается к этому API для всех операций с серверами.
Запускается отдельным процессом/контейнером.
"""

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
from minecraft_manager import (
    create_minecraft_container,
    start_container,
    stop_container,
    remove_container,
    get_container_status,
    ensure_network_exists,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

API_TOKEN = os.getenv("API_SECRET_TOKEN", "change_me_please")


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    ensure_network_exists()
    logger.info("API started")
    yield
    await db.close_pool()
    logger.info("API stopped")


app = FastAPI(title="MC Hosting API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth guard ─────────────────────────────────────────────────────────────────

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    return credentials.credentials


# ── Schemas ────────────────────────────────────────────────────────────────────

class CreateServerRequest(BaseModel):
    telegram_id: int
    username: str | None = None
    version: str          # paper / spigot / vanilla
    ram: str              # 1GB / 2GB / 4GB


class ServerActionRequest(BaseModel):
    telegram_id: int
    server_id: int


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _get_server_owned_by(server_id: int, telegram_id: int) -> dict:
    """Получает сервер и проверяет, что он принадлежит пользователю."""
    user = await db.get_user_by_telegram_id(telegram_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    server = await db.get_server(server_id)
    if not server or server["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Server not found or access denied")
    return server


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/servers/create", dependencies=[Depends(verify_token)])
async def create_server(req: CreateServerRequest):
    """Регистрирует пользователя, создаёт запись в БД и запускает контейнер."""
    user = await db.upsert_user(req.telegram_id, req.username)
    port = await db.get_next_port()

    # Создаём запись в БД
    server = await db.create_server(user["id"], req.ram, req.version)
    server_id = server["id"]

    try:
        result = await create_minecraft_container(server_id, req.version, req.ram, port)
    except Exception as e:
        logger.exception("Failed to create container for server %d", server_id)
        await db.update_server(server_id, status="error")
        raise HTTPException(status_code=500, detail=f"Container creation failed: {e}")

    # Сохраняем данные контейнера
    updated = await db.update_server(
        server_id,
        container_id=result["container_id"],
        ip=result["ip"],
        port=result["port"],
        status="running",
    )
    return updated


@app.get("/servers/{telegram_id}", dependencies=[Depends(verify_token)])
async def list_servers(telegram_id: int):
    """Возвращает список серверов пользователя."""
    user = await db.get_user_by_telegram_id(telegram_id)
    if not user:
        return []
    return await db.get_servers_by_user(user["id"])


@app.post("/servers/start", dependencies=[Depends(verify_token)])
async def start_server(req: ServerActionRequest):
    server = await _get_server_owned_by(req.server_id, req.telegram_id)
    if not server.get("container_id"):
        raise HTTPException(status_code=400, detail="No container associated")
    ok = await start_container(server["container_id"])
    if ok:
        await db.update_server(req.server_id, status="running")
    return {"success": ok}


@app.post("/servers/stop", dependencies=[Depends(verify_token)])
async def stop_server(req: ServerActionRequest):
    server = await _get_server_owned_by(req.server_id, req.telegram_id)
    if not server.get("container_id"):
        raise HTTPException(status_code=400, detail="No container associated")
    ok = await stop_container(server["container_id"])
    if ok:
        await db.update_server(req.server_id, status="stopped")
    return {"success": ok}


@app.delete("/servers/{server_id}", dependencies=[Depends(verify_token)])
async def delete_server(server_id: int, telegram_id: int):
    server = await _get_server_owned_by(server_id, telegram_id)
    if server.get("container_id"):
        await remove_container(server["container_id"])
    await db.delete_server(server_id)
    return {"success": True}


@app.get("/servers/{server_id}/status", dependencies=[Depends(verify_token)])
async def server_status(server_id: int, telegram_id: int):
    server = await _get_server_owned_by(server_id, telegram_id)
    if not server.get("container_id"):
        return {"status": "no_container"}
    container_info = await get_container_status(server["container_id"])
    # Синхронизируем статус в БД
    await db.update_server(server_id, status=container_info["status"])
    return {**server, **container_info}


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
