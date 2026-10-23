"""定时任务处理器：豆瓣订阅、Emby 同步、盘搜补漏、115/影巢签到、TG 补搜、缺集刷新。"""
import asyncio
import time

from .. import config
from ..database import db, event, log
from ..core import douban, emby, hdhive, pan115, nullbr, notify, tmdb


def _record(task_id: int, result: str) -> None:
    d = db()
    d.execute("UPDATE tasks SET last_run=?, last_result=? WHERE id=?", (time.time(), result[:500], task_id))
    d.commit()


async def run_job(job_type: str, task_id: int = 0, cfg: dict | None = None) -> str:
    """统一任务入口，返回结果摘要。"""
    cfg = cfg or {}
    try:
        if job_type == "checkin115":
            r = await job_checkin115()
        elif job_type == "checkin_hdhive":
            r = await job_checkin_hdhive()
        elif job_type == "douban":
            r = await job_douban(cfg)
        elif job_type == "emby_sync":
            r = await job_emby_sync()
        elif job_type == "missing_scan":
            from .subscribe import refresh_all_missing, mark_archived_done
            await refresh_all_missing()
            n = mark_archived_done()
            r = f"缺集刷新完成，归档 {n} 条"
        elif job_type == "nullbr":
            r = await job_pan_search(cfg)
        elif job_type == "tg_search":
            r = await job_tg_search(cfg)
        elif job_type == "strm_sync":
            from .sync115 import full_sync
            r = str(await full_sync(int(cfg["sync_path_id"])))
        elif job_type == "incr_sync":
            from .sync115 import incremental_sync
            r = str(await incremental_sync())
        elif job_type == "organize":
            from .organizer import organize_once
            r = str(await organize_once(int(cfg["rule_id"])))
        else:
            r = f"未知任务类型 {job_type}"
    except Exception as e:
        r = f"执行失败: {e}"
        log("ERROR", "tasks", f"任务 {job_type} 失败: {e}")
    if task_id:
        _record(task_id, r)
    log("INFO", "tasks", f"任务[{job_type}] 结果: {r}")
    return r


async def job_checkin115() -> str:
    r = await pan115.checkin()
    state = r.get("state")
    msg = f"115 签到 {'成功' if state else '失败'}: {r.get('error') or r.get('msg') or r}"
    await notify.send(msg, "每日签到")
    return msg


async def job_checkin_hdhive() -> str:
    r = await hdhive.checkin()
    data = r.get("data") or {}
    msg = f"影巢签到: {data.get('message') or r.get('message') or r}"
    await notify.send(msg, "每日签到")
    return msg


async def job_douban(cfg: dict) -> str:
    """豆瓣「想看」+ 榜单 + 自定义片单 → 自动订阅。"""
    from .subscribe import add_subscription
    added = 0
    items: list[dict] = []
    if cfg.get("wishlist", True):
        items += await douban.wishlist()
    for ch in cfg.get("charts", []):
        items += await douban.chart(ch, limit=int(cfg.get("limit", 20)))
    for url in cfg.get("custom_urls", []):
        items += await douban.custom_list(url)
    for it in items:
        # 豆瓣条目 → TMDB 匹配
        if not config.get("tmdb.api_key"):
            add_subscription("movie", it["title"], douban_id=it["douban_id"], source="douban")
            added += 1
            continue
        try:
            res = await tmdb.search(it["title"], "multi")
            if res:
                best = res[0]
                add_subscription(best["media_type"], best["title"], tmdb_id=best["tmdb_id"],
                                 douban_id=it["douban_id"], year=best["year"], poster=best["poster"],
                                 source="douban")
                added += 1
        except Exception as e:
            log("WARN", "douban", f"《{it['title']}》TMDB 匹配失败: {e}")
        await asyncio.sleep(0.3)
    msg = f"豆瓣同步: 抓取 {len(items)} 条，新增订阅 {added}"
    event("douban_sync", {"fetched": len(items), "added": added})
    return msg


async def job_emby_sync() -> str:
    """Emby 媒体库同步：发现收藏不全的剧集自动订阅（复刻 Media Automator 媒体库同步）。"""
    from .subscribe import add_subscription
    lib = await emby.library_by_tmdb()
    added = 0
    for tid, info in lib.items():
        if info["type"] != "Series":
            continue
        try:
            total, all_eps = await tmdb.episode_count(tid)
            have = set()
            for v in (info["episodes"] or {}).values():
                have.update(v)
            if all_eps and set(all_eps) - have:
                d = await tmdb.detail("tv", tid)
                add_subscription("tv", d.get("name", str(tid)), tmdb_id=tid,
                                 year=(d.get("first_air_date") or "")[:4],
                                 poster=("https://image.tmdb.org/t/p/w300" + d["poster_path"]) if d.get("poster_path") else "",
                                 source="emby")
                added += 1
        except Exception as e:
            log("WARN", "emby_sync", f"剧集 {tid} 检查失败: {e}")
    msg = f"Emby 同步: 库内剧集 {sum(1 for i in lib.values() if i['type']=='Series')}，新增补缺订阅 {added}"
    event("emby_sync", {"added": added})
    return msg


async def job_pan_search(cfg: dict) -> str:
    """盘搜补漏：对缺失订阅走 Nullbr/自定义盘搜/影巢找资源。"""
    from .matcher import record_resource
    from .subscribe import refresh_missing
    d = db()
    subs = d.execute("SELECT * FROM subscriptions WHERE status IN ('missing','partial')").fetchall()
    found = 0
    for sub in subs:
        query = f"{sub['title']} {sub['year'] or ''}".strip()
        results = await nullbr.search_all(query)
        # 影巢
        if sub["tmdb_id"] and config.get("hdhive.api_key"):
            try:
                for res in await hdhive.resources(sub["media_type"], sub["tmdb_id"]):
                    if res.get("unlock_points") in (None, 0) or cfg.get("hdhive_unlock"):
                        u = await hdhive.unlock(res["slug"])
                        link = u.get("full_url") or u.get("url")
                        if link:
                            results.append({"title": res.get("title", query), "link": link,
                                            "link_type": "115", "size": res.get("share_size", ""),
                                            "resolution": (res.get("video_resolution") or [""])[0] if isinstance(res.get("video_resolution"), list) else ""})
            except Exception as e:
                log("WARN", "hdhive", f"影巢查询失败: {e}")
        from .matcher import match_resource, push_to_target
        for r in results:
            for decision in match_resource(f"{r['title']} {r.get('resolution','')}", r["link"], only_missing=True):
                ok = await push_to_target(decision, cfg.get("push_target", "pan115"))
                if ok:
                    found += 1
        await asyncio.sleep(0.5)
    msg = f"盘搜补漏: 扫描 {len(subs)} 个缺失订阅，推送 {found} 条资源"
    event("pan_search", {"subs": len(subs), "pushed": found})
    return msg


async def job_tg_search(cfg: dict) -> str:
    """TG 主动搜索补漏：用缺集订阅标题在指定群组搜索。"""
    from .tg_monitor import tg_search
    chat_ids = [int(x) for x in cfg.get("chat_ids", [])]
    if not chat_ids:
        return "未配置搜索群组"
    d = db()
    subs = d.execute("SELECT * FROM subscriptions WHERE status IN ('missing','partial') LIMIT 30").fetchall()
    total = 0
    for sub in subs:
        results = await tg_search(sub["title"], chat_ids, limit=10)
        total += len(results)
        await asyncio.sleep(1)
    return f"TG 主动搜索: {len(subs)} 个订阅，命中 {total} 条消息（已入缓存并匹配）"
