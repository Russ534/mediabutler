"""115 → 本地 STRM 同步：全量同步（复刻 CMS full-sync）+ 增量同步（生活事件驱动）。"""
import time
from pathlib import Path

from .. import config
from ..database import db, event, log
from ..core import pan115, strm


async def full_sync(path_id: int) -> dict:
    """对一个 sync_path 做全量同步：视频生成 strm，元数据文件（nfo/图/字幕）按原名下载占位记录。
    返回统计。注意：一个目录只需成功全量一次，之后靠增量。"""
    d = db()
    sp = d.execute("SELECT * FROM sync_paths WHERE id=?", (path_id,)).fetchone()
    if not sp:
        return {"error": "sync_path 不存在"}
    log("INFO", "sync", f"开始全量同步: {sp['name']} (cid={sp['pan_cid']}) → {sp['local_dir']}")
    stats = {"video": 0, "meta": 0, "skip": 0, "dir": 0}
    known = strm.known_fids()
    seen: set[str] = set()

    async def _walk_local(cid: str, rel: str):
        async for it in pan115.walk(cid):
            if it["is_dir"]:
                stats["dir"] += 1
                continue
            seen.add(it["fid"])
            ext = Path(it["name"]).suffix.lower()
            # 用相对路径还原网盘目录层级
            if ext in pan115.VIDEO_EXT:
                if it["fid"] in known:
                    stats["skip"] += 1
                    continue
                if not it["pickcode"]:
                    stats["skip"] += 1
                    continue
                local = strm.write_strm(sp["local_dir"], it["name"], it["pickcode"])
                strm.record_strm(it["fid"], it["pickcode"], it["cid"], local, it["size"], it["sha1"])
                stats["video"] += 1
            elif ext in pan115.META_EXT and sp["with_meta"]:
                # 元数据文件直接下载到本地（小文件）
                try:
                    await _download_meta(it, sp["local_dir"])
                    stats["meta"] += 1
                except Exception as e:
                    log("WARN", "sync", f"元数据下载失败 {it['name']}: {e}")

    await _walk_local(str(sp["pan_cid"]), "")
    # 清理网盘已删除的
    orphan = known - seen
    if orphan:
        dd = db()
        rows = dd.execute(
            f"SELECT pan_fid FROM strm_files WHERE pan_fid IN ({','.join('?' * len(orphan))})",
            tuple(orphan)).fetchall()
        # 只清理属于本 sync_path 的（local_path 前缀匹配）
        prefix = str(Path(config.MEDIA_ROOT) / sp["local_dir"].lstrip("/"))
        for r in rows:
            row = dd.execute("SELECT local_path FROM strm_files WHERE pan_fid=?", (r["pan_fid"],)).fetchone()
            if row and row["local_path"].startswith(prefix):
                strm.remove_strm(r["pan_fid"])
    d.execute("UPDATE sync_paths SET last_full_sync=? WHERE id=?", (time.time(), path_id))
    d.commit()
    event("full_sync_done", {"path": sp["name"], **stats})
    log("INFO", "sync", f"全量同步完成 {sp['name']}: 视频{stats['video']} 元数据{stats['meta']} 跳过{stats['skip']}")
    return stats


async def _download_meta(item: dict, local_dir: str) -> None:
    """下载网盘小文件（nfo/字幕/图片）到本地。"""
    import httpx
    from ..core.pan115 import download_url
    url = await download_url(item["pickcode"])
    d = Path(config.MEDIA_ROOT) / local_dir.lstrip("/")
    d.mkdir(parents=True, exist_ok=True)
    target = d / strm.safe_name(item["name"])
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(url)
        if r.status_code == 200:
            target.write_bytes(r.content)


async def incremental_sync() -> dict:
    """增量同步：消费 115 生活事件（新文件→生成 strm，删除→删 strm）。
    依赖 115 生活事件已开启（APP 里打开）。重命名不产生事件（115 限制），影响不大。"""
    from ..config import get, set as cfg_set
    cursor = int(get("pan115.life_cursor", "0") or 0)
    try:
        r = await pan115.life_events(cursor=cursor, limit=1000)
    except Exception as e:
        log("ERROR", "sync", f"生活事件拉取失败: {e}")
        return {"error": str(e)}
    events = r.get("data", {}).get("list") or r.get("list") or []
    handled = 0
    latest = cursor
    d = db()
    sync_paths = d.execute("SELECT * FROM sync_paths WHERE enabled=1").fetchall()
    for ev in events:
        ev_id = str(ev.get("id") or ev.get("behavior_id") or "")
        cur = ev.get("cursor") or ev.get("create_time") or 0
        try:
            latest = max(latest, int(cur))
        except (TypeError, ValueError):
            pass
        if not ev_id:
            continue
        if d.execute("SELECT 1 FROM life_events WHERE id=?", (ev_id,)).fetchone():
            continue
        name = ev.get("file_name") or ev.get("name") or ""
        fid = str(ev.get("file_id") or ev.get("fid") or "")
        cid = str(ev.get("cid") or ev.get("parent_id") or "")
        ev_type = str(ev.get("type") or ev.get("behavior") or "")
        pickcode = ev.get("pick_code") or ev.get("pc") or ""
        d.execute("INSERT OR IGNORE INTO life_events(id,type,file_name,pan_cid,pan_fid,handled,created_at) VALUES(?,?,?,?,?,0,?)",
                  (ev_id, ev_type, name, cid, fid, time.time()))
        # 判断归属哪个 sync_path
        for sp in sync_paths:
            if cid != str(sp["pan_cid"]):
                continue  # 简化：只处理同步根目录直属事件；子目录事件由周期全量兜底
            ext = Path(name).suffix.lower()
            if ext not in pan115.VIDEO_EXT:
                continue
            if ev_type in ("new_file", "upload", "receive", "move_in", "add") and pickcode:
                local = strm.write_strm(sp["local_dir"], name, pickcode)
                strm.record_strm(fid, pickcode, cid, local, int(ev.get("size") or 0), ev.get("sha1") or "")
                handled += 1
            elif ev_type in ("delete", "del"):
                strm.remove_strm(fid)
                handled += 1
        d.execute("UPDATE life_events SET handled=1 WHERE id=?", (ev_id,))
    d.commit()
    cfg_set("pan115.life_cursor", str(latest))
    if handled:
        event("incr_sync", {"handled": handled})
    return {"events": len(events), "handled": handled, "cursor": latest}
