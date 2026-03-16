"""
bot.py — Telegram-бот для управления Minecraft-серверами.

Архитектура:
  - aiogram 3.x FSM для многошагового создания сервера
  - ReplyKeyboard = главное меню (постоянные кнопки внизу экрана)
  - InlineKeyboard = управление каждым конкретным сервером
  - Все операции с серверами делаются через внутренний FastAPI (api.py)
"""

import asyncio
import logging
import os

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
API_TOKEN    = os.getenv("API_SECRET_TOKEN", "change_me_please")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher(storage=MemoryStorage())


# ── FSM states ─────────────────────────────────────────────────────────────────

class CreateServer(StatesGroup):
    choosing_version = State()
    choosing_ram     = State()
    creating         = State()


# ── HTTP helper ────────────────────────────────────────────────────────────────

def _headers():
    return {"Authorization": f"Bearer {API_TOKEN}"}


async def api_get(path: str, params: dict | None = None) -> dict | list | None:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{API_BASE_URL}{path}", headers=_headers(), params=params) as r:
                if r.status == 200:
                    return await r.json()
                logger.error("GET %s → %d: %s", path, r.status, await r.text())
        except aiohttp.ClientError as e:
            logger.error("GET %s failed: %s", path, e)
    return None


async def api_post(path: str, json: dict) -> dict | None:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(f"{API_BASE_URL}{path}", headers=_headers(), json=json) as r:
                if r.status == 200:
                    return await r.json()
                logger.error("POST %s → %d: %s", path, r.status, await r.text())
        except aiohttp.ClientError as e:
            logger.error("POST %s failed: %s", path, e)
    return None


async def api_delete(path: str, params: dict | None = None) -> dict | None:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.delete(f"{API_BASE_URL}{path}", headers=_headers(), params=params) as r:
                if r.status == 200:
                    return await r.json()
                logger.error("DELETE %s → %d: %s", path, r.status, await r.text())
        except aiohttp.ClientError as e:
            logger.error("DELETE %s failed: %s", path, e)
    return None


# ── Keyboards ──────────────────────────────────────────────────────────────────

def main_menu() -> ReplyKeyboardMarkup:
    """Главное меню — постоянные кнопки внизу экрана."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎮 Мои серверы"),  KeyboardButton(text="➕ Создать сервер")],
            [KeyboardButton(text="📊 Статистика"),   KeyboardButton(text="⚙ Настройки")],
        ],
        resize_keyboard=True,
    )


def server_inline(server_id: int) -> InlineKeyboardMarkup:
    """Инлайн-кнопки управления конкретным сервером."""
    sid = str(server_id)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="▶ Старт",  callback_data=f"start:{sid}"),
        InlineKeyboardButton(text="⏹ Стоп",   callback_data=f"stop:{sid}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete:{sid}"),
        InlineKeyboardButton(text="📶 Статус", callback_data=f"status:{sid}"),
    ]])


def version_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Paper",   callback_data="ver:paper"),
        InlineKeyboardButton(text="Spigot",  callback_data="ver:spigot"),
        InlineKeyboardButton(text="Vanilla", callback_data="ver:vanilla"),
    ]])


def ram_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="1 GB", callback_data="ram:1GB"),
        InlineKeyboardButton(text="2 GB", callback_data="ram:2GB"),
        InlineKeyboardButton(text="4 GB", callback_data="ram:4GB"),
    ]])


# ── Status emoji helper ────────────────────────────────────────────────────────

STATUS_EMOJI = {
    "running": "🟢",
    "stopped": "🔴",
    "creating": "🟡",
    "error": "❌",
    "exited": "🔴",
}


def status_badge(status: str) -> str:
    return STATUS_EMOJI.get(status, "⚪") + " " + status


# ── Handlers ───────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    name = message.from_user.first_name or "Игрок"
    await message.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        "Я помогу тебе запустить собственный <b>Minecraft-сервер</b> в облаке.\n"
        "Используй меню ниже 👇",
        reply_markup=main_menu(),
    )


@dp.message(F.text == "🎮 Мои серверы")
async def show_my_servers(message: Message):
    tg_id = message.from_user.id
    servers = await api_get(f"/servers/{tg_id}")
    if not servers:
        await message.answer("У тебя пока нет серверов. Нажми ➕ <b>Создать сервер</b>!")
        return

    await message.answer("🎮 <b>Твои серверы:</b>")
    for srv in servers:
        text = (
            f"<b>#{srv['id']} {srv['version'].upper()} | {srv['ram']}</b>\n"
            f"🌐 IP: <code>{srv['ip'] or '—'}:{srv['port'] or '—'}</code>\n"
            f"Статус: {status_badge(srv['status'])}"
        )
        await message.answer(text, reply_markup=server_inline(srv["id"]))


# ── Create server — step 1: version ───────────────────────────────────────────

@dp.message(F.text == "➕ Создать сервер")
async def create_server_step1(message: Message, state: FSMContext):
    await state.set_state(CreateServer.choosing_version)
    await message.answer(
        "Выбери тип сервера:",
        reply_markup=version_keyboard(),
    )


@dp.callback_query(CreateServer.choosing_version, F.data.startswith("ver:"))
async def create_server_step2(callback: CallbackQuery, state: FSMContext):
    version = callback.data.split(":")[1]
    await state.update_data(version=version)
    await state.set_state(CreateServer.choosing_ram)
    await callback.message.edit_text(
        f"✅ Тип: <b>{version.capitalize()}</b>\n\nТеперь выбери количество RAM:",
        reply_markup=ram_keyboard(),
    )
    await callback.answer()


# ── Create server — step 2: RAM ────────────────────────────────────────────────

@dp.callback_query(CreateServer.choosing_ram, F.data.startswith("ram:"))
async def create_server_step3(callback: CallbackQuery, state: FSMContext):
    ram = callback.data.split(":")[1]
    data = await state.get_data()
    version = data["version"]

    await state.set_state(CreateServer.creating)
    await callback.message.edit_text(
        f"✅ RAM: <b>{ram}</b>\n\n⏳ Создаю сервер <b>{version.capitalize()} {ram}</b>...\n"
        "Это займёт около 30–60 секунд."
    )
    await callback.answer()

    # Вызываем API
    result = await api_post("/servers/create", {
        "telegram_id": callback.from_user.id,
        "username": callback.from_user.username,
        "version": version,
        "ram": ram,
    })

    await state.clear()

    if result and result.get("id"):
        srv = result
        await callback.message.answer(
            f"🎉 <b>Сервер создан!</b>\n\n"
            f"<b>Версия:</b> {srv['version'].capitalize()}\n"
            f"<b>RAM:</b> {srv['ram']}\n"
            f"<b>IP:</b> <code>{srv['ip'] or '—'}</code>\n"
            f"<b>Порт:</b> <code>{srv['port'] or '—'}</code>\n"
            f"<b>Статус:</b> {status_badge(srv['status'])}\n\n"
            f"Подключайся: <code>{srv['ip']}:{srv['port']}</code>",
            reply_markup=server_inline(srv["id"]),
        )
    else:
        await callback.message.answer(
            "❌ Не удалось создать сервер. Попробуй позже или обратись в поддержку."
        )


# ── Server inline actions ──────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("start:"))
async def action_start(callback: CallbackQuery):
    server_id = int(callback.data.split(":")[1])
    await callback.answer("⏳ Запускаю...")
    result = await api_post("/servers/start", {
        "telegram_id": callback.from_user.id,
        "server_id": server_id,
    })
    if result and result.get("success"):
        await callback.message.reply("▶️ Сервер запущен!")
    else:
        await callback.message.reply("❌ Не удалось запустить сервер.")


@dp.callback_query(F.data.startswith("stop:"))
async def action_stop(callback: CallbackQuery):
    server_id = int(callback.data.split(":")[1])
    await callback.answer("⏳ Останавливаю...")
    result = await api_post("/servers/stop", {
        "telegram_id": callback.from_user.id,
        "server_id": server_id,
    })
    if result and result.get("success"):
        await callback.message.reply("⏹ Сервер остановлен.")
    else:
        await callback.message.reply("❌ Не удалось остановить сервер.")


@dp.callback_query(F.data.startswith("delete:"))
async def action_delete(callback: CallbackQuery):
    server_id = int(callback.data.split(":")[1])
    await callback.answer("⏳ Удаляю...")
    result = await api_delete(
        f"/servers/{server_id}",
        params={"telegram_id": callback.from_user.id}
    )
    if result and result.get("success"):
        await callback.message.edit_text("🗑 Сервер удалён.")
    else:
        await callback.message.reply("❌ Не удалось удалить сервер.")


@dp.callback_query(F.data.startswith("status:"))
async def action_status(callback: CallbackQuery):
    server_id = int(callback.data.split(":")[1])
    await callback.answer("⏳ Получаю статус...")
    result = await api_get(
        f"/servers/{server_id}/status",
        params={"telegram_id": callback.from_user.id}
    )
    if result:
        text = (
            f"📶 <b>Статус сервера #{server_id}</b>\n\n"
            f"Версия: {result.get('version', '—').capitalize()}\n"
            f"RAM: {result.get('ram', '—')}\n"
            f"IP: <code>{result.get('ip', '—')}:{result.get('port', '—')}</code>\n"
            f"Статус: {status_badge(result.get('status', 'unknown'))}\n"
            f"Запущен: {result.get('started_at', '—')}"
        )
        await callback.message.reply(text)
    else:
        await callback.message.reply("❌ Не удалось получить статус.")


# ── Stats ──────────────────────────────────────────────────────────────────────

@dp.message(F.text == "📊 Статистика")
async def show_stats(message: Message):
    tg_id = message.from_user.id
    servers = await api_get(f"/servers/{tg_id}") or []
    total   = len(servers)
    running = sum(1 for s in servers if s.get("status") == "running")
    stopped = sum(1 for s in servers if s.get("status") in ("stopped", "exited"))
    await message.answer(
        f"📊 <b>Твоя статистика</b>\n\n"
        f"Всего серверов: <b>{total}</b>\n"
        f"🟢 Запущено: <b>{running}</b>\n"
        f"🔴 Остановлено: <b>{stopped}</b>"
    )


# ── Settings ───────────────────────────────────────────────────────────────────

@dp.message(F.text == "⚙ Настройки")
async def show_settings(message: Message):
    await message.answer(
        "⚙ <b>Настройки</b>\n\n"
        "На данный момент дополнительные настройки недоступны.\n"
        "Следите за обновлениями!"
    )


# ── Fallback ───────────────────────────────────────────────────────────────────

@dp.message()
async def fallback(message: Message):
    await message.answer("Используй меню ниже 👇", reply_markup=main_menu())


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    logger.info("Bot starting...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
