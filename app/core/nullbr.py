"""盘搜：Nullbr API + 自定义盘搜（pansou 类）。搜 115/磁力/电驴/夸克资源。"""
from .. import config
from ..database import log
from .http import client


async def nullbr_search(query: str, page: int = 1) -> list[dict]:
    """Nullbr 资源搜索。返回 [{title, link, link_type, size, resolution}]"""
    base = config.get("nullbr.base_url").rstrip("/")
    key = config.get("nullbr.api_key")
    if not base:
        return []
    out = []
    try:
        async with client(timeout=20) as c:
            # Nullbr 搜索接口（tmdb 搜索 + 资源列表两段式，这里用关键词直达）
            r = await c.get(f"{base}/search", params={"q": query, "page": page},
                            headers={"X-Api-Key": key} if key else {})
            if r.status_code != 200:
                log("WARN", "nullbr", f"搜索失败 HTTP {r.status_code}")
                return []
            data = r.json()
            items = data.get("results") or data.get("data") or []
            for it in items:
                link = it.get("url") or it.get("link") or ""
                out.append({
                    "title": it.get("title") or it.get("name") or "",
                    "link": link,
                    "link_type": _link_type(link),
                    "size": str(it.get("size") or ""),
                    "resolution": it.get("resolution") or "",
                })
    except Exception as e:
        log("ERROR", "nullbr", f"搜索异常: {e}")
    return out


async def pansou_search(query: str) -> list[dict]:
    """自定义盘搜服务（兼容 pansou 的 /api/search 返回格式）。"""
    base = config.get("pansou.base_url").rstrip("/")
    if not base:
        return []
    out = []
    try:
        async with client(timeout=25) as c:
            r = await c.post(f"{base}/api/search", json={"kw": query, "res": "all", "src": "all"})
            if r.status_code != 200:
                r = await c.get(f"{base}/api/search", params={"kw": query})
            data = r.json()
            merged = (data.get("data") or {}).get("merged_by_type") or {}
            for pan, items in merged.items():
                for it in items:
                    out.append({
                        "title": it.get("note") or it.get("title") or query,
                        "link": it.get("url") or "",
                        "link_type": pan,
                        "size": "",
                        "resolution": "",
                    })
    except Exception as e:
        log("ERROR", "pansou", f"盘搜异常: {e}")
    return out


def _link_type(link: str) -> str:
    if not link:
        return "unknown"
    l = link.lower()
    if l.startswith("magnet:"):
        return "magnet"
    if l.startswith("ed2k:"):
        return "ed2k"
    if "115.com" in l:
        return "115"
    if "alipan" in l or "aliyundrive" in l:
        return "aliyun"
    if "pan.quark" in l:
        return "quark"
    if "pan.baidu" in l:
        return "baidu"
    if "123pan" in l or "123684" in l or "123865" in l:
        return "123"
    return "other"


async def search_all(query: str) -> list[dict]:
    """聚合所有盘搜源。"""
    results = await nullbr_search(query) + await pansou_search(query)
    seen, out = set(), []
    for r in results:
        if r["link"] and r["link"] not in seen:
            seen.add(r["link"])
            out.append(r)
    return out
