"""数据接管：把 CMS / CineChill 在飞牛上的存量沉淀导入 MediaButler。
- CMS cms-online.db: cloud_data(50万行云端文件索引) / sub_media1(订阅) / sub_source(TG频道)
- CMS category.yaml / tidy.yaml / words_replace.txt → 分类规则 / 洗版策略 / 识别词表
- chill rss_tasks.json 的 entry_tmdb_map(649条 TMDB 映射) → 订阅
只读源数据，绝不写老容器的数据。"""
import json
import re
import sqlite3
import time
from pathlib import Path

from .. import config
from ..database import db, log

CMS_DB = Path(config.MEDIA_ROOT) / "cms" / "config" / "cms-online.db"
CMS_CFG = Path(config.MEDIA_ROOT) / "cms" / "config"
CHILL_CFG = Path(config.MEDIA_ROOT) / "chill" / "config"

# CMS sub_media1.status → 本地订阅状态（2=已完结入库）
_STATUS_MAP = {0: "missing", 1: "partial", 2: "in_library", 3: "archived"}


def preview() -> dict:
    """扫描可接管的资产，不动数据。"""
    out: dict = {"cms_db": CMS_DB.exists(), "cloud_rows": 0, "cms_subs": 0, "cms_tg_channels": [],
                 "cms_rules": {}, "chill_rss": 0, "chill_rss_tasks": []}
    if CMS_DB.exists():
        try:
            c = sqlite3.connect(f"file:{CMS_DB}?mode=ro", uri=True)
            out["cloud_rows"] = c.execute("SELECT COUNT(*) FROM cloud_data").fetchone()[0]
            out["cms_subs"] = c.execute("SELECT COUNT(*) FROM sub_media1").fetchone()[0]
            out["cms_tg_channels"] = [r[0] for r in c.execute(
                "SELECT sub_url FROM sub_source WHERE type='tg_channel' AND status=1")]
            c.close()
        except Exception as e:
            out["cms_error"] = str(e)
    for f, key in [("category.yaml", "category"), ("tidy.yaml", "tidy"), ("words_replace.txt", "words")]:
        p = CMS_CFG / f
        out["cms_rules"][key] = p.exists()
    rss = CHILL_CFG / "rss_tasks.json"
    if rss.exists():
        try:
            tasks = json.loads(rss.read_text(encoding="utf-8"))
            out["chill_rss_tasks"] = [t.get("name") for t in tasks]
            out["chill_rss"] = sum(len(t.get("entry_tmdb_map") or {}) for t in tasks)
        except Exception as e:
            out["chill_error"] = str(e)
    return out


def import_all() -> dict:
    stats = {"cloud_index": 0, "subscriptions": 0, "tg_rules": 0, "chill_subs": 0,
             "rules": [], "skipped": 0, "errors": []}
    d = db()
    now = time.time()

    # 1. 云端文件索引（增量同步基线 + 无效清理对照）
    if CMS_DB.exists():
        try:
            c = sqlite3.connect(f"file:{CMS_DB}?mode=ro", uri=True)
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT fid,pid,name,full_y_path,pick_code,sha1,size,is_dir FROM cloud_data").fetchall()
            c.close()
            batch = [(str(r["fid"]), str(r["pid"] or ""), r["name"], r["full_y_path"] or "",
                      r["pick_code"] or "", r["sha1"] or "", int(r["size"] or 0),
                      1 if r["is_dir"] else 0, now) for r in rows]
            d.executemany(
                "INSERT INTO cloud_index(fid,pid,name,full_path,pickcode,sha1,size,is_dir,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(fid) DO UPDATE SET "
                "pid=excluded.pid,name=excluded.name,full_path=excluded.full_path,"
                "pickcode=excluded.pickcode,sha1=excluded.sha1,size=excluded.size,"
                "is_dir=excluded.is_dir,updated_at=excluded.updated_at", batch)
            d.commit()
            stats["cloud_index"] = len(batch)
        except Exception as e:
            stats["errors"].append(f"cloud_data: {e}")
            log("ERROR", "legacy", f"cloud_data 导入失败: {e}")

        # 2. CMS 订阅
        try:
            c = sqlite3.connect(f"file:{CMS_DB}?mode=ro", uri=True)
            c.row_factory = sqlite3.Row
            subs = c.execute("SELECT * FROM sub_media1").fetchall()
            channels = c.execute(
                "SELECT sub_name,sub_url FROM sub_source WHERE type='tg_channel' AND status=1").fetchall()
            c.close()
            for s in subs:
                if s["tmdb_id"] and d.execute(
                        "SELECT 1 FROM subscriptions WHERE tmdb_id=? AND media_type=?",
                        (s["tmdb_id"], s["type"])).fetchone():
                    stats["skipped"] += 1
                    continue
                d.execute(
                    "INSERT INTO subscriptions(media_type,tmdb_id,title,year,poster,status,source,note,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (s["type"] or "movie", s["tmdb_id"], s["title"] or "", str(s["year"] or ""),
                     s["poster_path"] or "", _STATUS_MAP.get(s["status"], "missing"),
                     "cms", "接管自 CMS", now, now))
                stats["subscriptions"] += 1
            # 3. CMS TG 频道 → 监控规则
            for ch in channels:
                name = ch["sub_name"] or ch["sub_url"] or ""
                if d.execute("SELECT 1 FROM tg_rules WHERE name=?", (name,)).fetchone():
                    continue
                d.execute(
                    "INSERT INTO tg_rules(name,channel_ids,push_target,only_missing,enabled) VALUES(?,?,?,?,1)",
                    (f"{name}（接管自CMS）", json.dumps([ch["sub_url"]]), "pan115", 1))
                stats["tg_rules"] += 1
            d.commit()
        except Exception as e:
            stats["errors"].append(f"sub_media: {e}")
            log("ERROR", "legacy", f"CMS 订阅导入失败: {e}")

    # 4. CMS 规则文件 → 设置
    for fname, key, label in [("category.yaml", "organize.category_yaml", "分类规则"),
                              ("tidy.yaml", "wash.tidy_yaml", "洗版策略")]:
        p = CMS_CFG / fname
        if p.exists():
            try:
                config.set(key, p.read_text(encoding="utf-8"))
                stats["rules"].append(label)
            except Exception as e:
                stats["errors"].append(f"{fname}: {e}")
    wp = CMS_CFG / "words_replace.txt"
    if wp.exists():
        try:
            dst = config.BASE_DIR / "word_rules.txt"
            old = wp.read_text(encoding="utf-8")
            if dst.exists():
                old = dst.read_text(encoding="utf-8") + "\n# ---- 接管自 CMS ----\n" + old
            dst.write_text(old, encoding="utf-8")
            stats["rules"].append("识别词表")
        except Exception as e:
            stats["errors"].append(f"words_replace: {e}")

    # 5. chill RSS 订阅映射
    rss = CHILL_CFG / "rss_tasks.json"
    if rss.exists():
        try:
            tasks = json.loads(rss.read_text(encoding="utf-8"))
            for t in tasks:
                if not t.get("enabled"):
                    continue
                tname = t.get("name") or "chill"
                for _k, items in (t.get("entry_tmdb_map") or {}).items():
                    for it in items:
                        tid = it.get("tmdb_id")
                        mtype = "movie" if it.get("type") == "Movie" else "tv"
                        if not tid:
                            continue
                        if d.execute("SELECT 1 FROM subscriptions WHERE tmdb_id=? AND media_type=?",
                                     (tid, mtype)).fetchone():
                            stats["skipped"] += 1
                            continue
                        d.execute(
                            "INSERT INTO subscriptions(media_type,tmdb_id,title,year,status,source,note,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (mtype, tid, it.get("title") or "", str(it.get("year") or ""),
                             "missing", "chill-rss", f"接管自 chill RSS「{tname}」", now, now))
                        stats["chill_subs"] += 1
            d.commit()
        except Exception as e:
            stats["errors"].append(f"chill rss: {e}")

    d.execute("INSERT INTO legacy_imports(source,kind,detail,created_at) VALUES(?,?,?,?)",
              ("cms+chill", "import_all", json.dumps(stats, ensure_ascii=False)[:3900], now))
    d.commit()
    log("INFO", "legacy", f"数据接管完成: {stats}")
    return stats
