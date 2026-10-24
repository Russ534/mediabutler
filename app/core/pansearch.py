"""盘搜：pansou（默认 https://pansou.qianshan.app）聚合搜索 115/夸克/阿里/百度/磁力资源。
接口格式：POST /api/search {"kw":..., "res":"merge", "src":"all"} → data.merged_by_type."""
import asyncio

from .. import config
from ..database import log
from .http import client

PAN_NAME_MAP = {
    "115": "115", "quark": "quark", "aliyun": "aliyun", "baidu": "baidu",
    "magnet": "magnet", "ed2k": "ed2k", "123": "123", "xunlei": "xunlei",
    "uc": "uc", "tianyi": "tianyi", "mobile": "mobile", "pikpak": "pikpak",
}


def _link_type(link: str) -> str:
    if not link:
        return "unknown"
    l = link.lower()
    if l.startswith("magnet:"):
        return "magnet"
    if l.startswith("ed2k:"):
        return "ed2k"
    if "115.com" in l or "115cdn.com" in l or "anxia.com" in l or "share.115.com" in l:
        return "115"
    if "alipan" in l or "aliyundrive" in l:
        return "aliyun"
    if "pan.quark" in l:
        return "quark"
    if "pan.baidu" in l:
        return "baidu"
    if "123pan" in l or "123684" in l or "123865" in l or "123912" in l:
        return "123"
    if "xunlei" in l or "pan.xunlei" in l:
        return "xunlei"
    return "other"


async def pansou_search(query: str, base: str = "") -> list[dict]:
    """调用 pansou 实例搜索。返回 [{title, link, link_type, password, size, source}]"""
    base = (base or config.get("pansou.base_url") or "https://pansou.qianshan.app").rstrip("/")
    out = []
    last_err = None
    # 盘搜站偶发超时（空错误信息），重试 2 次
    for attempt in range(3):
        try:
            async with client(timeout=45) as c:
                r = await c.post(f"{base}/api/search",
                                 json={"kw": query, "res": "merge", "src": "all", "cloud_types": []})
                if r.status_code != 200:
                    # 兼容 GET 形式
                    r = await c.get(f"{base}/api/search", params={"kw": query})
                r.raise_for_status()
                data = r.json()
            last_err = None
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            log("ERROR", "pansou", f"盘搜异常({base}): {e}")
            return out
    try:
        merged = (data.get("data") or {}).get("merged_by_type") or {}
    except Exception as e:
        log("ERROR", "pansou", f"盘搜响应解析失败({base}): {e}")
        return out
    for pan, items in merged.items():
        if not isinstance(items, list):
            continue
        for it in items:
            link = it.get("url") or ""
            if not link:
                continue
            out.append({
                "title": it.get("note") or it.get("title") or query,
                "link": link,
                "link_type": PAN_NAME_MAP.get(pan) or _link_type(link),
                "password": it.get("password") or "",
                "size": "",
                "resolution": "",
                "source": it.get("source") or pan,
                "datetime": it.get("datetime") or "",
            })
    return out


async def search_all(query: str) -> list[dict]:
    """聚合主盘搜 + 可选第二盘搜源，按链接去重。"""
    results = await pansou_search(query)
    second = config.get("pansou.base_url2")
    if second:
        results += await pansou_search(query, second)
    seen, out = set(), []
    for r in results:
        if r["link"] not in seen:
            seen.add(r["link"])
            out.append(r)
    return out
