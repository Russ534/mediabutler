"""Emby API：连接测试、媒体库扫描、已有剧集盘点、刷新、webhook 处理。"""
from .. import config
from ..database import log
from .http import client


def _base():
    return config.get("emby.host").rstrip("/")


def _params(**kw):
    kw["api_key"] = config.get("emby.api_key")
    return kw


async def test() -> dict:
    async with client(timeout=15) as c:
        r = await c.get(_base() + "/System/Info", params=_params())
        r.raise_for_status()
        info = r.json()
        libs = await c.get(_base() + "/Library/VirtualFolders", params=_params())
        return {"version": info.get("Version"), "server_name": info.get("ServerName"),
                "libraries": len(libs.json()) if libs.status_code == 200 else 0}


async def items(parent_id: str = "", include_types: str = "Series,Movie", fields: str = "ProviderIds,Path", limit: int = 10000):
    async with client(timeout=60) as c:
        r = await c.get(_base() + "/Items", params=_params(
            ParentId=parent_id, IncludeItemTypes=include_types, Fields=fields,
            Recursive="true", Limit=limit))
        r.raise_for_status()
        return r.json().get("Items", [])


async def series_episodes(series_id: str):
    async with client(timeout=60) as c:
        r = await c.get(_base() + "/Items", params=_params(
            ParentId=series_id, IncludeItemTypes="Episode", Recursive="true",
            Fields="IndexNumber,ParentIndexNumber,Path", Limit=5000))
        r.raise_for_status()
        return r.json().get("Items", [])


async def library_by_tmdb() -> dict[int, dict]:
    """盘点 Emby 中所有带 TMDB ID 的影视：{tmdb_id: {type, emby_id, episodes:{season:[ep]}}}"""
    result: dict[int, dict] = {}
    try:
        for it in await items():
            ids = it.get("ProviderIds") or {}
            tid = ids.get("Tmdb")
            if not tid:
                continue
            entry = result.setdefault(int(tid), {"type": it.get("Type"), "emby_id": it["Id"], "episodes": {}})
            if it.get("Type") == "Series":
                for ep in await series_episodes(it["Id"]):
                    s, e = ep.get("ParentIndexNumber"), ep.get("IndexNumber")
                    if s and e:
                        entry["episodes"].setdefault(s, []).append(e)
    except Exception as e:
        log("ERROR", "emby", f"媒体库盘点失败: {e}")
    return result


async def refresh_library() -> bool:
    try:
        async with client(timeout=30) as c:
            r = await c.post(_base() + "/Library/Refresh", params=_params())
            return r.status_code in (200, 204)
    except Exception as e:
        log("ERROR", "emby", f"触发全库刷新失败: {e}")
        return False


async def refresh_item(item_id: str) -> bool:
    try:
        async with client(timeout=30) as c:
            r = await c.post(f"{_base()}/Items/{item_id}/Refresh",
                             params=_params(Recursive="true", MetadataRefreshMode="Default", ImageRefreshMode="Default"))
            return r.status_code in (200, 204)
    except Exception:
        return False


def parse_webhook(payload: dict) -> dict | None:
    """解析 Emby webhook，入库事件返回 {tmdb_id, media_type, title}。"""
    try:
        if payload.get("Event") not in ("library.new", "item.added"):
            return None
        item = payload.get("Item") or {}
        ids = item.get("ProviderIds") or {}
        tid = ids.get("Tmdb") or ids.get("tmdb")
        if not tid:
            return None
        return {"tmdb_id": int(tid),
                "media_type": "movie" if item.get("Type") == "Movie" else "tv",
                "title": item.get("Name", "")}
    except Exception:
        return None
