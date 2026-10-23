"""订阅引擎：状态机 missing → partial/in_library → washing → archived（复刻 Media Automator）。"""
import json
import time
from datetime import datetime, timedelta

from ..database import db, event, log
from ..core import tmdb, emby


def add_subscription(media_type: str, title: str, tmdb_id: int | None = None,
                     douban_id: str = "", year: str = "", poster: str = "",
                     season: int | None = None, wash_days: int = 0,
                     keywords: str = "", source: str = "manual") -> int:
    d = db()
    # 去重
    if tmdb_id:
        row = d.execute(
            "SELECT id FROM subscriptions WHERE tmdb_id=? AND media_type=? AND COALESCE(season,-1)=COALESCE(?,-1)",
            (tmdb_id, media_type, season)).fetchone()
        if row:
            return row["id"]
    now = time.time()
    cur = d.execute(
        "INSERT INTO subscriptions(media_type,tmdb_id,douban_id,title,year,poster,season,status,"
        "wash_days,wash_until,keywords,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (media_type, tmdb_id, douban_id, title, year, poster, season, "missing",
         wash_days, "", keywords, source, now, now))
    d.commit()
    sub_id = cur.lastrowid
    event("subscribe_add", {"id": sub_id, "title": title, "source": source})
    log("INFO", "subscribe", f"新增订阅[{source}]: {title} ({media_type} {tmdb_id})")
    return sub_id


async def refresh_missing(sub_id: int) -> None:
    """对照 TMDB 总集数与 Emby 已有集数，刷新缺集清单与状态。"""
    d = db()
    sub = d.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    if not sub or sub["status"] == "archived":
        return
    if sub["media_type"] == "movie":
        lib = await emby.library_by_tmdb()
        have = sub["tmdb_id"] in lib if sub["tmdb_id"] else False
        missing: list[int] = [] if have else [1]
        total = 1
    else:
        total, all_eps = 0, []
        if sub["tmdb_id"]:
            try:
                total, all_eps = await tmdb.episode_count(sub["tmdb_id"], sub["season"])
            except Exception as e:
                log("WARN", "subscribe", f"TMDB 集数获取失败 {sub['title']}: {e}")
        lib = await emby.library_by_tmdb()
        have_eps: set[int] = set()
        if sub["tmdb_id"] and sub["tmdb_id"] in lib:
            eps_map = lib[sub["tmdb_id"]].get("episodes") or {}
            if sub["season"] is not None:
                have_eps = set(eps_map.get(sub["season"], []))
            else:
                for v in eps_map.values():
                    have_eps.update(v)
        missing = sorted(set(all_eps) - have_eps) if all_eps else []
    status = "missing" if missing else "in_library"
    if sub["status"] == "washing" and sub["wash_until"]:
        if datetime.now() < datetime.fromisoformat(sub["wash_until"]):
            status = "washing"
    # 完结判定：全集齐 → 若配置洗版则进入洗版期
    if not missing and sub["wash_days"] > 0 and sub["status"] not in ("washing", "archived"):
        wash_until = (datetime.now() + timedelta(days=sub["wash_days"])).isoformat()
        status = "washing"
        d.execute("UPDATE subscriptions SET wash_until=? WHERE id=?", (wash_until, sub_id))
        log("INFO", "subscribe", f"《{sub['title']}》已齐，进入 {sub['wash_days']} 天洗版期")
    elif status != "washing":
        d.execute("UPDATE subscriptions SET wash_until='' WHERE id=?", (sub_id,))
    d.execute("UPDATE subscriptions SET missing=?, total_episodes=?, status=?, updated_at=? WHERE id=?",
              (json.dumps(missing), total, status, time.time(), sub_id))
    d.commit()


async def refresh_all_missing() -> None:
    rows = db().execute("SELECT id FROM subscriptions WHERE status NOT IN ('archived')").fetchall()
    for r in rows:
        try:
            await refresh_missing(r["id"])
        except Exception as e:
            log("ERROR", "subscribe", f"缺集刷新失败 sub#{r['id']}: {e}")


def mark_archived_done() -> int:
    """洗版期到期的自动归档。"""
    d = db()
    rows = d.execute("SELECT id,title,wash_until FROM subscriptions WHERE status='washing'").fetchall()
    n = 0
    for r in rows:
        if r["wash_until"] and datetime.now() >= datetime.fromisoformat(r["wash_until"]):
            d.execute("UPDATE subscriptions SET status='archived', updated_at=? WHERE id=?",
                      (time.time(), r["id"]))
            log("INFO", "subscribe", f"《{r['title']}》洗版期结束，自动归档")
            n += 1
    d.commit()
    return n
