"""网盘自动整理（复刻 CineChill 一条龙/CMS 自动整理）：
监听 115「待整理」目录 → 解析文件名 → TMDB 匹配 → 重命名 → 移动到分类目录。"""
import re
import time
from pathlib import Path

from ..database import db, event, log
from ..core import pan115, parser, tmdb


async def organize_once(rule_id: int) -> dict:
    d = db()
    rule = d.execute("SELECT * FROM organize_rules WHERE id=?", (rule_id,)).fetchone()
    if not rule:
        return {"error": "规则不存在"}
    stats = {"scanned": 0, "moved": 0, "renamed": 0, "failed": 0, "skip": 0}
    watch_cid = rule["watch_cid"]
    async for it in pan115.walk(watch_cid, _max_depth=3):
        if it["is_dir"]:
            continue
        stats["scanned"] += 1
        ext = Path(it["name"]).suffix.lower()
        if ext not in pan115.VIDEO_EXT:
            stats["skip"] += 1
            continue
        # 关键词过滤
        kws = (rule["match_keywords"] or "").strip()
        if kws and not any(k.strip().lower() in it["name"].lower() for k in re.split(r"[,，]", kws) if k.strip()):
            stats["skip"] += 1
            continue
        parsed = parser.parse_title(it["name"])
        try:
            dest_cid, new_name = await _resolve_dest(rule, it, parsed, ext)
            if not dest_cid:
                stats["skip"] += 1
                continue
            if new_name != it["name"]:
                await pan115.rename(it["fid"], new_name)
                stats["renamed"] += 1
            if str(dest_cid) != str(it["cid"]):
                await pan115.move([it["fid"]], dest_cid)
                stats["moved"] += 1
            log("INFO", "organize", f"整理: {it['name']} → {new_name}")
        except Exception as e:
            stats["failed"] += 1
            log("ERROR", "organize", f"整理失败 {it['name']}: {e}")
    event("organize_done", {"rule": rule["name"], **stats})
    return stats


async def _resolve_dest(rule, item: dict, parsed: dict, ext: str) -> tuple[str, str]:
    """解析目标目录与新文件名。返回 (cid, new_name)，cid 为空表示跳过。"""
    guess = parsed["title_guess"]
    year = parsed["year"]
    media_type = rule["media_type"]
    # TMDB 搜索修正标题
    if config_tmdb_available():
        try:
            results = await tmdb.search(guess, "multi")
            if results:
                best = results[0]
                guess = best["title"] or guess
                year = best["year"] or year
                media_type = best["media_type"] if media_type == "auto" else media_type
        except Exception:
            pass
    if media_type == "auto":
        media_type = "tv" if parsed["season"] is not None or parsed["episodes"] else "movie"
    # 目标路径：target_cid / 类型分类 / 剧名 (年份)
    parts = []
    if media_type == "tv":
        parts.append("剧集")
    elif media_type == "movie":
        parts.append("电影")
    title_dir = f"{guess} ({year})" if year else guess
    parts.append(title_dir)
    dest_cid = await pan115.ensure_dir(parts, root=rule["target_cid"])
    # 新文件名
    if media_type == "tv" and parsed["season"] is not None:
        if parsed["episodes"]:
            ep = f"S{parsed['season']:02d}E{parsed['episodes'][0]:02d}"
            if len(parsed["episodes"]) > 1:
                ep += f"-E{parsed['episodes'][-1]:02d}"
        else:
            ep = f"S{parsed['season']:02d}"
        new_name = f"{guess}.{ep}.{parsed['resolution'] or ''}{ext}".replace("..", ".").strip(".")
    else:
        new_name = f"{title_dir}{ext}"
    return dest_cid, new_name


def config_tmdb_available() -> bool:
    from .. import config
    return bool(config.get("tmdb.api_key"))
