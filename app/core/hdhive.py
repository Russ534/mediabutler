"""影巢（HDHive）Open API：签到、资源查询、解锁。文档见 media-automator 影巢 api 文档。"""
from .. import config
from .http import client

BASE = "https://hdhive.com/api/open"


def _headers():
    return {"X-API-Key": config.get("hdhive.api_key")}


async def ping() -> dict:
    async with client(timeout=15) as c:
        r = await c.get(BASE + "/ping", headers=_headers())
        r.raise_for_status()
        return r.json()


async def me() -> dict:
    async with client(timeout=15) as c:
        r = await c.get(BASE + "/me", headers=_headers())
        r.raise_for_status()
        return r.json()


async def checkin(gambler: bool | None = None) -> dict:
    """每日签到。gambler=True 赌狗模式（-3~+30），默认普通（+4~+10）。"""
    if gambler is None:
        gambler = config.get("hdhive.gambler") == "true"
    async with client(timeout=20) as c:
        r = await c.post(BASE + "/checkin", headers=_headers(), json={"is_gambler": gambler})
        r.raise_for_status()
        return r.json()


async def resources(media_type: str, tmdb_id: int) -> list[dict]:
    async with client(timeout=20) as c:
        r = await c.get(f"{BASE}/resources/{media_type}/{tmdb_id}", headers=_headers())
        r.raise_for_status()
        return (r.json().get("data") or {}).get("resources", r.json().get("data") or [])


async def unlock(slug: str) -> dict:
    async with client(timeout=20) as c:
        r = await c.post(BASE + "/resources/unlock", headers=_headers(), json={"slug": slug})
        r.raise_for_status()
        return r.json().get("data") or {}
