"""APScheduler 定时调度：从 tasks 表加载 cron 任务。"""
import json

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .database import db, log

scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

DEFAULT_TASKS = [
    ("每日115签到", "checkin115", "5 8 * * *", {}),
    ("每日影巢签到", "checkin_hdhive", "10 8 * * *", {}),
    ("豆瓣想看同步", "douban", "*/30 * * * *", {"wishlist": True, "charts": [], "custom_urls": []}),
    ("Emby媒体库同步", "emby_sync", "*/20 * * * *", {}),
    ("缺集刷新", "missing_scan", "*/15 * * * *", {}),
    ("盘搜补漏", "pan_search", "0 */2 * * *", {"push_target": "pan115", "hdhive_unlock": False}),
    ("115增量同步", "incr_sync", "* * * * *", {}),
]


def seed_default_tasks() -> None:
    d = db()
    for name, jt, cron, cfg in DEFAULT_TASKS:
        if not d.execute("SELECT 1 FROM tasks WHERE job_type=? AND name=?", (jt, name)).fetchone():
            d.execute("INSERT INTO tasks(name,job_type,cron,enabled,config) VALUES(?,?,?,1,?)",
                      (name, jt, cron, json.dumps(cfg, ensure_ascii=False)))
    d.commit()


def _make_runner(task_id: int, job_type: str, cfg: dict):
    async def _run():
        from .services.tasks import run_job
        await run_job(job_type, task_id, cfg)
    return _run


def reload_jobs() -> None:
    """从 DB 重载所有任务。"""
    scheduler.remove_all_jobs()
    d = db()
    for t in d.execute("SELECT * FROM tasks WHERE enabled=1 AND cron!=''").fetchall():
        try:
            cfg = json.loads(t["config"] or "{}")
            scheduler.add_job(_make_runner(t["id"], t["job_type"], cfg),
                              CronTrigger.from_crontab(t["cron"]),
                              id=f"task_{t['id']}", replace_existing=True,
                              misfire_grace_time=300, coalesce=True)
        except Exception as e:
            log("ERROR", "scheduler", f"任务 {t['name']} cron 无效({t['cron']}): {e}")
    log("INFO", "scheduler", f"已加载 {len(scheduler.get_jobs())} 个定时任务")


def start() -> None:
    seed_default_tasks()
    scheduler.start()
    reload_jobs()
