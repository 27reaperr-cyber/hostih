"""
minecraft_manager.py — Создание и управление Docker-контейнерами Minecraft.

Каждый сервер живёт в отдельном контейнере на образе itzg/minecraft-server.
IP контейнера определяется через Docker SDK после старта.
"""

import asyncio
import logging
import os
from typing import Optional

import docker
from docker.errors import DockerException, NotFound, APIError

logger = logging.getLogger(__name__)

# Сеть Docker, в которой живут контейнеры
DOCKER_NETWORK = os.getenv("DOCKER_NETWORK", "minecraft_net")

# Соответствие версий Minecraft образам
VERSION_MAP = {
    "paper":   "PAPER",
    "spigot":  "SPIGOT",
    "vanilla": "VANILLA",
}

RAM_MAP = {
    "1GB": {"xms": "512M", "xmx": "1G",  "mem": "1.2g"},
    "2GB": {"xms": "1G",   "xmx": "2G",  "mem": "2.4g"},
    "4GB": {"xms": "2G",   "xmx": "4G",  "mem": "4.8g"},
}


def _get_client() -> docker.DockerClient:
    """Получает Docker-клиент (сокет пробрасывается в контейнер бота)."""
    return docker.from_env()


async def create_minecraft_container(server_id: int, version: str, ram: str, port: int) -> dict:
    """
    Создаёт и запускает контейнер Minecraft.

    Возвращает:
        {"container_id": str, "ip": str, "port": int}
    """
    client = _get_client()
    ver_type = VERSION_MAP.get(version.lower(), "PAPER")
    mem = RAM_MAP.get(ram, RAM_MAP["2GB"])

    container_name = f"mc_server_{server_id}"

    # Убираем старый контейнер с тем же именем, если есть
    try:
        old = client.containers.get(container_name)
        old.remove(force=True)
        logger.info("Removed stale container %s", container_name)
    except NotFound:
        pass

    logger.info("Creating container %s (version=%s ram=%s port=%d)", container_name, version, ram, port)

    container = await asyncio.to_thread(
        client.containers.run,
        image="itzg/minecraft-server:latest",
        name=container_name,
        detach=True,
        environment={
            "EULA": "TRUE",
            "TYPE": ver_type,
            "MEMORY": mem["xmx"],
            "JVM_XX_OPTS": f"-Xms{mem['xms']} -Xmx{mem['xmx']}",
            "ONLINE_MODE": "FALSE",   # упрощает подключение без premium аккаунта
        },
        ports={"25565/tcp": port},
        mem_limit=mem["mem"],
        network=DOCKER_NETWORK,
        restart_policy={"Name": "unless-stopped"},
        labels={
            "mc.server_id": str(server_id),
            "mc.managed": "true",
        }
    )

    # Получаем IP контейнера в сети
    container.reload()
    ip = _get_container_ip(container)

    return {
        "container_id": container.id,
        "ip": ip,
        "port": port,
    }


def _get_container_ip(container) -> str:
    """Извлекает IP-адрес контейнера из сетевых настроек Docker."""
    try:
        networks = container.attrs["NetworkSettings"]["Networks"]
        if DOCKER_NETWORK in networks:
            return networks[DOCKER_NETWORK]["IPAddress"]
        # Fallback: первая попавшаяся сеть
        for net_data in networks.values():
            ip = net_data.get("IPAddress", "")
            if ip:
                return ip
    except (KeyError, TypeError):
        pass
    return "127.0.0.1"


async def start_container(container_id: str) -> bool:
    """Запускает остановленный контейнер."""
    try:
        client = _get_client()
        container = await asyncio.to_thread(client.containers.get, container_id)
        await asyncio.to_thread(container.start)
        logger.info("Started container %s", container_id)
        return True
    except (NotFound, APIError, DockerException) as e:
        logger.error("start_container error: %s", e)
        return False


async def stop_container(container_id: str) -> bool:
    """Останавливает запущенный контейнер."""
    try:
        client = _get_client()
        container = await asyncio.to_thread(client.containers.get, container_id)
        await asyncio.to_thread(container.stop, timeout=30)
        logger.info("Stopped container %s", container_id)
        return True
    except (NotFound, APIError, DockerException) as e:
        logger.error("stop_container error: %s", e)
        return False


async def remove_container(container_id: str) -> bool:
    """Удаляет контейнер вместе с данными."""
    try:
        client = _get_client()
        container = await asyncio.to_thread(client.containers.get, container_id)
        await asyncio.to_thread(container.remove, force=True, v=True)
        logger.info("Removed container %s", container_id)
        return True
    except (NotFound, APIError, DockerException) as e:
        logger.error("remove_container error: %s", e)
        return False


async def get_container_status(container_id: str) -> dict:
    """
    Возвращает статус и аптайм контейнера.

    Returns:
        {"status": str, "started_at": str | None, "uptime_seconds": int}
    """
    try:
        client = _get_client()
        container = await asyncio.to_thread(client.containers.get, container_id)
        container.reload()
        state = container.attrs["State"]
        status = state.get("Status", "unknown")
        started_at = state.get("StartedAt", None)
        return {"status": status, "started_at": started_at}
    except NotFound:
        return {"status": "removed", "started_at": None}
    except (APIError, DockerException) as e:
        logger.error("get_container_status error: %s", e)
        return {"status": "error", "started_at": None}


def ensure_network_exists():
    """Создаёт docker-сеть для Minecraft-контейнеров, если она ещё не создана."""
    try:
        client = _get_client()
        existing = [n.name for n in client.networks.list()]
        if DOCKER_NETWORK not in existing:
            client.networks.create(DOCKER_NETWORK, driver="bridge")
            logger.info("Created docker network: %s", DOCKER_NETWORK)
    except DockerException as e:
        logger.warning("Could not ensure docker network: %s", e)
