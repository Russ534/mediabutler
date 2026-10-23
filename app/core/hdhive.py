"""影巢（HDHive，现域名 re0.me）Open API：签到、资源查询、解锁。

注意：re0.me 套了 Cloudflare，机房 IP 直连会被拦（403 challenge），
在家庭宽带/NAS 上需要走代理（配置 hdhive.proxy，如 http://192.168.31.201:7890）。
文档见 media-automator 影巢 api 文档。
"""
from .. import config
from .http import client


def _base() -> str:
    return (config.get("hdhive.base_url") or "https://re0.me/api/open").rstrip("/")


def _headers() -> dict:
    return {"X-API-Key": config.get("hdhive.api_key")}


def _client(timeout: float = 20.0):
    # hdhive 专用代理优先，其次全局兜底代理
    return client("hdhive.proxy", timeout=timeout)


async def ping() -> dict:
    async with _client(15) as c:
        r = await c.get(_base() + "/ping", headers=_headers())
        r.raise_for_status()
        return r.json()


async def me() -> dict:
    async with _client(15) as c:
        r = await c.get(_base() + "/me", headers=_headers())
        r.raise_for_status()
        return r.json()


async def checkin(gambler: bool | None = None) -> dict:
    """每日签到。gambler=True 赌狗模式（-3~+30），默认普通（+4~+10）。"""
    if gambler is None:
        gambler = config.get("hdhive.gambler") == "true"
    async with _client(20) as c:
        r = await c.post(_base() + "/checkin", headers=_headers(), json={"is_gambler": gambler})
        r.raise_for_status()
        return r.json()


async def resources(media_type: str, tmdb_id: int) -> list[dict]:
    async with _client(20) as c:
        r = await c.get(f"{_base()}/resources/{media_type}/{tmdb_id}", headers=_headers())
        r.raise_for_status()
        return (r.json().get("data") or {}).get("resources", r.json().get("data") or [])


async def unlock(slug: str) -> dict:
    async with _client(20) as c:
        r = await c.post(_base() + "/resources/unlock", headers=_headers(), json={"slug": slug})
        r.raise_for_status()
        return r.json().get("data") or {}
