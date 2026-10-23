"""业务 API：仪表盘、订阅、资源、任务、同步、整理、TG、设置、日志、webhook。"""
import json
import time

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel

from .. import config
from ..database import db, event, log
from .auth import require

router = APIRouter(prefix="/api")


def _auth(authorization: str):
    require(authorization)


# ---------- 仪表盘 ----------

@router.get("/dashboard")
async def dashboard(authorization: str = Header("")):
    _auth(authorization)
    d = db()
    subs = d.execute("SELECT status, COUNT(*) c FROM subscriptions GROUP BY status").fetchall()
    res = d.execute("SELECT status, COUNT(*) c FROM resources GROUP BY status").fetchall()
    tg_count = d.execute("SELECT COUNT(*) c FROM tg_cache").fetchone()["c"]
    strm_count = d.execute("SELECT COUNT(*) c FROM strm_files").fetchone()["c"]
    events = [dict(r) for r in d.execute("SELECT * FROM events ORDER BY id DESC LIMIT 30").fetchall()]
    tasks = [dict(r) for r in d.execute("SELECT * FROM tasks ORDER BY id").fetchall()]
    from ..scheduler import scheduler
    jobs = [{"id": j.id, "next": j.next_run_time.isoformat() if j.next_run_time else None}
            for j in scheduler.get_jobs()]
    return {"subscriptions": {r["status"]: r["c"] for r in subs},
            "resources": {r["status"]: r["c"] for r in res},
            "tg_cache": tg_count, "strm_files": strm_count,
            "events": events, "tasks": tasks, "jobs": jobs}


# ---------- 订阅 ----------

class SubBody(BaseModel):
    media_type: str
    title: str
    tmdb_id: int | None = None
    season: int | None = None
    year: str = ""
    poster: str = ""
    wash_days: int = 0
    keywords: str = ""


@router.get("/subscriptions")
async def list_subs(status: str = "", authorization: str = Header("")):
    _auth(authorization)
    d = db()
    if status:
        rows = d.execute("SELECT * FROM subscriptions WHERE status=? ORDER BY updated_at DESC", (status,)).fetchall()
    else:
        rows = d.execute("SELECT * FROM subscriptions ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


@router.post("/subscriptions")
async def add_sub(body: SubBody, authorization: str = Header("")):
    _auth(authorization)
    from ..services.subscribe import add_subscription
    sid = add_subscription(body.media_type, body.title, body.tmdb_id, year=body.year,
                           poster=body.poster, season=body.season, wash_days=body.wash_days,
                           keywords=body.keywords)
    return {"id": sid}


@router.delete("/subscriptions/{sid}")
async def del_sub(sid: int, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    d.execute("DELETE FROM subscriptions WHERE id=?", (sid,))
    d.commit()
    return {"ok": True}


@router.post("/subscriptions/{sid}/refresh")
async def refresh_sub(sid: int, authorization: str = Header("")):
    _auth(authorization)
    from ..services.subscribe import refresh_missing
    await refresh_missing(sid)
    return {"ok": True}


@router.get("/tmdb/search")
async def tmdb_search(q: str, authorization: str = Header("")):
    _auth(authorization)
    from ..core import tmdb
    return await tmdb.search(q)


# ---------- 资源记录 ----------

@router.get("/resources")
async def list_resources(sub_id: int = 0, limit: int = 100, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    if sub_id:
        rows = d.execute("SELECT * FROM resources WHERE sub_id=? ORDER BY id DESC LIMIT ?", (sub_id, limit)).fetchall()
    else:
        rows = d.execute("SELECT * FROM resources ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------- 任务 ----------

class TaskBody(BaseModel):
    name: str
    job_type: str
    cron: str = ""
    enabled: bool = True
    config: dict = {}


@router.post("/tasks")
async def add_task(body: TaskBody, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    d.execute("INSERT INTO tasks(name,job_type,cron,enabled,config) VALUES(?,?,?,?,?)",
              (body.name, body.job_type, body.cron, int(body.enabled),
               json.dumps(body.config, ensure_ascii=False)))
    d.commit()
    from ..scheduler import reload_jobs
    reload_jobs()
    return {"ok": True}


@router.post("/tasks/{tid}/toggle")
async def toggle_task(tid: int, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    d.execute("UPDATE tasks SET enabled=1-enabled WHERE id=?", (tid,))
    d.commit()
    from ..scheduler import reload_jobs
    reload_jobs()
    return {"ok": True}


@router.post("/tasks/{tid}/run")
async def run_task(tid: int, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    t = d.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        return {"error": "任务不存在"}
    import asyncio
    from ..services.tasks import run_job
    asyncio.create_task(run_job(t["job_type"], t["id"], json.loads(t["config"] or "{}")))
    return {"ok": True, "msg": "已触发，稍后查看任务结果"}


@router.delete("/tasks/{tid}")
async def del_task(tid: int, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    d.execute("DELETE FROM tasks WHERE id=?", (tid,))
    d.commit()
    from ..scheduler import reload_jobs
    reload_jobs()
    return {"ok": True}


# ---------- STRM 同步路径 ----------

class SyncPathBody(BaseModel):
    name: str
    pan_cid: str = "0"
    local_dir: str
    media_type: str = "tv"
    with_meta: bool = True


@router.get("/sync/paths")
async def list_sync_paths(authorization: str = Header("")):
    _auth(authorization)
    return [dict(r) for r in db().execute("SELECT * FROM sync_paths ORDER BY id").fetchall()]


@router.post("/sync/paths")
async def add_sync_path(body: SyncPathBody, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    d.execute("INSERT INTO sync_paths(name,pan_cid,local_dir,media_type,with_meta) VALUES(?,?,?,?,?)",
              (body.name, body.pan_cid, body.local_dir, body.media_type, int(body.with_meta)))
    d.commit()
    return {"ok": True}


@router.delete("/sync/paths/{pid}")
async def del_sync_path(pid: int, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    d.execute("DELETE FROM sync_paths WHERE id=?", (pid,))
    d.commit()
    return {"ok": True}


@router.post("/sync/paths/{pid}/full")
async def full_sync_path(pid: int, authorization: str = Header("")):
    _auth(authorization)
    import asyncio
    from ..services.sync115 import full_sync
    asyncio.create_task(full_sync(pid))
    return {"ok": True, "msg": "全量同步已在后台开始，查看日志跟踪进度"}


@router.get("/pan115/dir")
async def pan_dir(cid: str = "0", authorization: str = Header("")):
    _auth(authorization)
    from ..core import pan115
    r = await pan115.list_dir(cid, limit=200)
    items = []
    for it in (r.get("data") or []):
        items.append({"cid" if not it.get("pc") else "fid": str(it.get("cid") or it.get("fid")),
                      "name": it.get("n"), "is_dir": not bool(it.get("pc")), "size": it.get("s")})
    return items


# ---------- 整理规则 ----------

class OrganizeRuleBody(BaseModel):
    name: str
    watch_cid: str
    target_cid: str
    match_keywords: str = ""
    media_type: str = "auto"
    enabled: bool = True


@router.get("/organize/rules")
async def list_rules(authorization: str = Header("")):
    _auth(authorization)
    return [dict(r) for r in db().execute("SELECT * FROM organize_rules ORDER BY id").fetchall()]


@router.post("/organize/rules")
async def add_rule(body: OrganizeRuleBody, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    d.execute("INSERT INTO organize_rules(name,watch_cid,target_cid,match_keywords,media_type,enabled) VALUES(?,?,?,?,?,?)",
              (body.name, body.watch_cid, body.target_cid, body.match_keywords, body.media_type, int(body.enabled)))
    d.commit()
    return {"ok": True}


@router.delete("/organize/rules/{rid}")
async def del_rule(rid: int, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    d.execute("DELETE FROM organize_rules WHERE id=?", (rid,))
    d.commit()
    return {"ok": True}


@router.post("/organize/rules/{rid}/run")
async def run_rule(rid: int, authorization: str = Header("")):
    _auth(authorization)
    import asyncio
    from ..services.organizer import organize_once
    asyncio.create_task(organize_once(rid))
    return {"ok": True, "msg": "整理任务已在后台开始"}


# ---------- TG ----------

@router.get("/tg/status")
async def tg_status(authorization: str = Header("")):
    _auth(authorization)
    from ..services.tg_monitor import get_client, configured
    c = await get_client() if configured() else None
    return {"configured": configured(), "authorized": c is not None}


class TgLoginStart(BaseModel):
    phone: str


class TgLoginFinish(BaseModel):
    phone: str
    code: str
    phone_code_hash: str
    password: str = ""


@router.post("/tg/login/start")
async def tg_login_start(body: TgLoginStart, authorization: str = Header("")):
    _auth(authorization)
    from ..services.tg_monitor import send_code
    h = await send_code(body.phone)
    return {"phone_code_hash": h}


@router.post("/tg/login/finish")
async def tg_login_finish(body: TgLoginFinish, authorization: str = Header("")):
    _auth(authorization)
    from ..services.tg_monitor import sign_in, start_listener
    ok = await sign_in(body.phone, body.code, body.phone_code_hash, body.password)
    if ok:
        import asyncio
        asyncio.create_task(start_listener())
    return {"ok": ok}


@router.get("/tg/dialogs")
async def tg_dialogs(authorization: str = Header("")):
    _auth(authorization)
    from ..services.tg_monitor import dialogs
    return await dialogs()


class TgRuleBody(BaseModel):
    name: str
    channel_ids: list[int]
    push_target: str = "pan115"
    forward_chat_id: str = ""
    only_missing: bool = True
    keywords: str = ""
    enabled: bool = True


@router.get("/tg/rules")
async def list_tg_rules(authorization: str = Header("")):
    _auth(authorization)
    return [dict(r) for r in db().execute("SELECT * FROM tg_rules ORDER BY id").fetchall()]


@router.post("/tg/rules")
async def add_tg_rule(body: TgRuleBody, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    d.execute("INSERT INTO tg_rules(name,channel_ids,push_target,forward_chat_id,only_missing,keywords,enabled) VALUES(?,?,?,?,?,?,?)",
              (body.name, json.dumps(body.channel_ids), body.push_target, body.forward_chat_id,
               int(body.only_missing), body.keywords, int(body.enabled)))
    d.commit()
    from ..services.tg_monitor import stop_listener, start_listener
    import asyncio
    asyncio.create_task(_restart_tg())
    return {"ok": True}


async def _restart_tg():
    from ..services.tg_monitor import stop_listener, start_listener
    await stop_listener()
    await start_listener()


@router.delete("/tg/rules/{rid}")
async def del_tg_rule(rid: int, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    d.execute("DELETE FROM tg_rules WHERE id=?", (rid,))
    d.commit()
    await _restart_tg()
    return {"ok": True}


@router.post("/tg/cache/{channel_id}")
async def tg_cache_history(channel_id: int, limit: int = 500, authorization: str = Header("")):
    _auth(authorization)
    import asyncio
    from ..services.tg_monitor import cache_history
    asyncio.create_task(cache_history(channel_id, limit))
    return {"ok": True, "msg": f"历史缓存任务已开始（最多 {limit} 条）"}


@router.get("/tg/search_cache")
async def tg_search_cache(q: str, authorization: str = Header("")):
    _auth(authorization)
    from ..services.tg_monitor import search_cached
    return await search_cached(q)


# ---------- 设置 / 测试 ----------

@router.get("/settings")
async def get_settings(authorization: str = Header("")):
    _auth(authorization)
    return config.all_settings(mask_secret=True)


class SettingsBody(BaseModel):
    values: dict


@router.post("/settings")
async def save_settings(body: SettingsBody, authorization: str = Header("")):
    _auth(authorization)
    for k, v in body.values.items():
        if isinstance(v, str) and "****" in v:
            continue  # 掩码值不回写
        config.set(k, str(v))
    return {"ok": True}


@router.post("/test/emby")
async def test_emby(authorization: str = Header("")):
    _auth(authorization)
    from ..core import emby
    return await emby.test()


@router.post("/test/pan115")
async def test_115(authorization: str = Header("")):
    _auth(authorization)
    from ..core import pan115
    return await pan115.test()


@router.post("/test/hdhive")
async def test_hdhive(authorization: str = Header("")):
    _auth(authorization)
    from ..core import hdhive
    return await hdhive.ping()


@router.post("/test/notify")
async def test_notify(authorization: str = Header("")):
    _auth(authorization)
    from ..core.notify import send
    return {"ok": await send("这是一条测试通知", "MediaButler")}


# ---------- 日志 ----------

@router.get("/logs")
async def get_logs(level: str = "", module: str = "", limit: int = 200, authorization: str = Header("")):
    _auth(authorization)
    d = db()
    sql = "SELECT * FROM logs WHERE 1=1"
    args: list = []
    if level:
        sql += " AND level=?"
        args.append(level)
    if module:
        sql += " AND module=?"
        args.append(module)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in d.execute(sql, args).fetchall()]


# ---------- Emby Webhook（免登录，靠路径混淆 + 可选 token） ----------

@router.post("/webhook/emby")
async def emby_webhook(request: Request):
    payload = await request.json()
    from ..core.emby import parse_webhook
    from ..services.subscribe import refresh_all_missing
    info = parse_webhook(payload)
    if info:
        log("INFO", "webhook", f"Emby 入库: {info['title']} (tmdb={info['tmdb_id']})")
        event("emby_webhook", info)
        import asyncio
        asyncio.create_task(refresh_all_missing())
    return {"ok": True}
