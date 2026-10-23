"""TMDB API：搜索、详情、剧集季集信息。"""
from .. import config
from .http import client

API = "https://api.themoviedb.org/3"


async def _get(path: str, **params):
    params.setdefault("api_key", config.get("tmdb.api_key"))
    params.setdefault("language", "zh-CN")
    async with client("tmdb.proxy") as c:
        r = await c.get(API + path, params=params)
        r.raise_for_status()
        return r.json()


async def search(query: str, media_type: str = "multi", page: int = 1):
    data = await _get(f"/search/{media_type}", query=query, page=page)
    items = [it for it in data.get("results", [])
             if (it.get("media_type") or media_type) in ("movie", "tv")]
    # Symedia 识别细节：按人气排序，避免冷门同名结果抢位
    items.sort(key=lambda it: it.get("popularity") or 0, reverse=True)
    out = []
    for it in items:
        mt = it.get("media_type") or media_type
        out.append({
            "tmdb_id": it["id"],
            "media_type": mt,
            "title": it.get("title") or it.get("name") or "",
            "original_title": it.get("original_title") or it.get("original_name") or "",
            "year": (it.get("release_date") or it.get("first_air_date") or "")[:4],
            "poster": ("https://image.tmdb.org/t/p/w300" + it["poster_path"]) if it.get("poster_path") else "",
            "overview": it.get("overview", ""),
        })
    return out


async def detail(media_type: str, tmdb_id: int):
    return await _get(f"/{media_type}/{tmdb_id}", append_to_response="external_ids")


async def season_detail(tmdb_id: int, season: int):
    return await _get(f"/tv/{tmdb_id}/season/{season}")


async def episode_count(tmdb_id: int, season: int | None = None) -> tuple[int, list[int]]:
    """返回 (总集数, 集号列表)。season=None 时统计所有正式季。"""
    d = await detail("tv", tmdb_id)
    seasons = [s for s in d.get("seasons", []) if s.get("season_number", 0) > 0]
    if season is not None:
        seasons = [s for s in seasons if s.get("season_number") == season]
    eps: list[int] = []
    for s in seasons:
        try:
            sd = await season_detail(tmdb_id, s["season_number"])
            eps.extend(e["episode_number"] for e in sd.get("episodes", []))
        except Exception:
            eps.extend(range(1, s.get("episode_count", 0) + 1))
    return len(eps), sorted(set(eps))
