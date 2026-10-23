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


def _err(e: Exception) -> dict:
    return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def ping() -> dict:
    if not config.get("hdhive.api_key"):
        return {"ok": False, "error": "未配置影巢 API Key（re0.me → 个人设置 → 开放接口生成）"}
    try:
        async with _client(15) as c:
            r = await c.get(_base() + "/ping", headers=_headers())
        if r.status_code == 200:
            j = r.json()
            j["ok"] = True
            return j
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return _err(e)


async def me() -> dict:
    async with _client(15) as c:
        r = await c.get(_base() + "/me", headers=_headers())
        r.raise_for_status()
        return r.json()


async def checkin(gambler: bool | None = None) -> dict:
    """每日签到。gambler=True 赌狗模式（-3~+30），默认普通（+4~+10）。失败不抛异常。"""
    if gambler is None:
        gambler = config.get("hdhive.gambler") == "true"
    if not config.get("hdhive.api_key"):
        return {"ok": False, "error": "未配置影巢 API Key"}
    try:
        async with _client(20) as c:
            r = await c.post(_base() + "/checkin", headers=_headers(), json={"is_gambler": gambler})
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200:
            j["ok"] = True
            return j
        return {"ok": False, "error": j.get("message") or f"HTTP {r.status_code}"}
    except Exception as e:
        return _err(e)


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
