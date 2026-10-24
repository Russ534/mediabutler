"""Emby API：连接测试、媒体库扫描、已有剧集盘点、刷新、webhook 处理。"""
import asyncio
import time

from .. import config
from ..database import log
from .http import client

# 盘点结果缓存：Emby 全库扫描很重，短时间内多次调用直接复用
_cache: dict = {"data": None, "ts": 0}
CACHE_TTL = 600


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


async def _get_items(params: dict, timeout: int = 60, retries: int = 2) -> list[dict]:
    """分页拉取 /Items：Emby 一次性拉上万条会 503，改成每页 500 + 重试退避。"""
    out: list[dict] = []
    start = 0
    page = 500
    async with client(timeout=timeout) as c:
        while True:
            p = dict(params, StartIndex=start, Limit=page)
            last_err = None
            for attempt in range(retries + 1):
                try:
                    r = await c.get(_base() + "/Items", params=_params(**p))
                    r.raise_for_status()
                    data = r.json()
                    batch = data.get("Items") or []
                    total = data.get("TotalRecordCount")
                    out.extend(batch)
                    if not batch or (total is not None and len(out) >= int(total)):
                        return out
                    start += len(batch)
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    if attempt < retries:
                        await asyncio.sleep(2 * (attempt + 1))
            if last_err:
                raise last_err


async def items(parent_id: str = "", include_types: str = "Series,Movie", fields: str = "ProviderIds,Path"):
    return await _get_items(dict(ParentId=parent_id, IncludeItemTypes=include_types,
                                 Fields=fields, Recursive="true"))


async def series_episodes(series_id: str):
    return await _get_items(dict(ParentId=series_id, IncludeItemTypes="Episode",
                                 Recursive="true",
                                 Fields="IndexNumber,ParentIndexNumber,Path"))


async def library_by_tmdb() -> dict[int, dict]:
    """盘点 Emby 中所有带 TMDB ID 的影视：{tmdb_id: {type, emby_id, episodes:{season:[ep]}}}
    全库扫描较重，结果缓存 10 分钟；单部剧集查询失败只跳过该剧，不影响整体。"""
    if _cache["data"] and time.time() - _cache["ts"] < CACHE_TTL:
        return _cache["data"]
    result: dict[int, dict] = {}
    failed = 0
    try:
        for it in await items():
            ids = it.get("ProviderIds") or {}
            tid = ids.get("Tmdb")
            if not tid:
                continue
            entry = result.setdefault(int(tid), {"type": it.get("Type"), "emby_id": it["Id"], "episodes": {}})
            if it.get("Type") == "Series":
                try:
                    for ep in await series_episodes(it["Id"]):
                        s, e = ep.get("ParentIndexNumber"), ep.get("IndexNumber")
                        if s and e:
                            entry["episodes"].setdefault(s, []).append(e)
                except Exception as e:
                    failed += 1
                    if failed <= 5:
                        log("WARN", "emby", f"剧集盘点失败 {it.get('Name')}: {type(e).__name__}")
    except Exception as e:
        log("ERROR", "emby", f"媒体库盘点失败: {e}")
        return _cache["data"] or result  # 全库失败时退回上一版缓存
    if result:
        _cache["data"], _cache["ts"] = result, time.time()
        log("INFO", "emby", f"媒体库盘点完成: {len(result)} 部，剧集失败 {failed}")
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
