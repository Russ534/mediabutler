"""豆瓣：「想看」列表、热门榜单抓取（复刻 Media Automator 豆瓣订阅源）。"""
import re

from .. import config
from ..database import log
from .http import client


def _headers():
    return {
        "User-Agent": config.get("douban.user_agent"),
        "Cookie": config.get("douban.cookie"),
        "Referer": "https://movie.douban.com/",
    }


async def wishlist(user_id: str = "", start: int = 0, count: int = 50) -> list[dict]:
    """抓取用户「想看」列表。需要豆瓣 cookie。"""
    uid = user_id or config.get("douban.user_id")
    if not uid:
        return []
    url = f"https://movie.douban.com/people/{uid}/wish"
    out = []
    async with client(timeout=20) as c:
        r = await c.get(url, params={"start": start, "sort": "time", "rating": "all", "filter": "all", "mode": "grid"}, headers=_headers())
        if r.status_code != 200:
            log("WARN", "douban", f"想看列表抓取失败 HTTP {r.status_code}")
            return []
        html = r.text
    # grid 模式每个 item 含链接与标题
    for m in re.finditer(r'href="https://movie\.douban\.com/subject/(\d+)/"[^>]*>\s*<img[^>]*alt="([^"]+)"', html):
        out.append({"douban_id": m.group(1), "title": m.group(2).strip()})
    if not out:  # 兜底 list 模式
        for m in re.finditer(r'href="https://movie\.douban\.com/subject/(\d+)/"[^>]*class="title"[^>]*>\s*<em>([^<]+)</em>', html):
            out.append({"douban_id": m.group(1), "title": m.group(2).strip()})
    return out


CHARTS = {
    "hot_movie": "https://movie.douban.com/chart",
    "top250": "https://movie.douban.com/top250",
}


async def chart(name: str = "top250", limit: int = 20) -> list[dict]:
    url = CHARTS.get(name, CHARTS["top250"])
    out = []
    async with client(timeout=20) as c:
        r = await c.get(url, headers=_headers())
        if r.status_code != 200:
            return []
        html = r.text
    for m in re.finditer(r'href="https://movie\.douban\.com/subject/(\d+)/".*?alt="([^"]+)"', html, re.S):
        out.append({"douban_id": m.group(1), "title": m.group(2).strip()})
        if len(out) >= limit:
            break
    return out


async def custom_list(url: str, limit: int = 50) -> list[dict]:
    """自定义豆列/片单 URL。"""
    out = []
    async with client(timeout=20) as c:
        r = await c.get(url, headers=_headers())
        if r.status_code != 200:
            return []
        html = r.text
    for m in re.finditer(r'https://movie\.douban\.com/subject/(\d+)/[^>]*>([^<]{1,60})<', html):
        did, title = m.group(1), m.group(2).strip()
        if title and not title.startswith("http"):
            out.append({"douban_id": did, "title": title})
        if len(out) >= limit:
            break
    return out
