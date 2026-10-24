"""115 → 本地 STRM 同步：全量同步（复刻 CMS full-sync）+ 增量同步（生活事件驱动）。"""
import os
import time
from pathlib import Path

from .. import config
from ..database import db, event, log
from ..core import pan115, strm

# 会产生「文件落入网盘」的生活事件行为类型；删除类行为名含 delete（show_type=1 已过滤无关事件）
_ADD_BEHAVIORS = {"upload_file", "upload_image_file", "receive_files", "move_file", "move_image_file"}


def cloud_tree(parent: str = "0") -> list[dict]:
    """从云端索引（cloud_index）取某目录的直接子节点。
    接管 CMS 的 50 万索引本地就在，秒开，不必打 115 API。"""
    d = db()
    rows = d.execute(
        "SELECT fid,name,is_dir FROM cloud_index WHERE pid=? ORDER BY is_dir DESC, name",
        (str(parent),)).fetchall()
    out = []
    for r in rows:
        child_dirs = d.execute("SELECT COUNT(*) c FROM cloud_index WHERE pid=? AND is_dir=1",
                               (r["fid"],)).fetchone()["c"]
        child_files = d.execute("SELECT COUNT(*) c FROM cloud_index WHERE pid=? AND is_dir=0",
                                (r["fid"],)).fetchone()["c"]
        out.append({"fid": r["fid"], "name": r["name"], "is_dir": bool(r["is_dir"]),
                    "dirs": child_dirs, "files": child_files})
    return out


def cloud_path(fid: str) -> str:
    """由 fid 回溯出云端完整路径（父链查找，最多 30 层防环）。"""
    d = db()
    parts, cur, guard = [], str(fid), 0
    while cur and cur != "0" and guard < 30:
        guard += 1
        row = d.execute("SELECT pid,name FROM cloud_index WHERE fid=?", (cur,)).fetchone()
        if not row:
            break
        parts.append(row["name"])
        cur = str(row["pid"])
    return "/" + "/".join(reversed(parts))


async def full_sync(path_id: int) -> dict:
    """对一个 sync_path 做全量同步：视频生成 strm（保留网盘目录层级），元数据文件按原名下载。
    同时登记 sync_dirs 目录映射（供增量同步路由子目录事件）。"""
    d = db()
    sp = d.execute("SELECT * FROM sync_paths WHERE id=?", (path_id,)).fetchone()
    if not sp:
        return {"error": "sync_path 不存在"}
    log("INFO", "sync", f"开始全量同步: {sp['name']} (cid={sp['pan_cid']}) → {sp['local_dir']}")
    stats = {"video": 0, "meta": 0, "skip": 0, "dir": 0}
    known = strm.known_fids()
    seen: set[str] = set()

    d.execute("DELETE FROM sync_dirs WHERE sync_path_id=?", (path_id,))
    d.execute("INSERT OR REPLACE INTO sync_dirs(sync_path_id,pan_cid,rel) VALUES(?,?,?)",
              (path_id, str(sp["pan_cid"]), ""))
    d.commit()

    async for it in pan115.walk(str(sp["pan_cid"])):
        if it["is_dir"]:
            stats["dir"] += 1
            dir_rel = f"{it['rel']}/{it['name']}" if it["rel"] else it["name"]
            d.execute("INSERT OR REPLACE INTO sync_dirs(sync_path_id,pan_cid,rel) VALUES(?,?,?)",
                      (path_id, it["fid"], dir_rel))
            continue
        seen.add(it["fid"])
        # 维护云端索引（接管 CMS cloud_data 后持续保鲜）
        d.execute(
            "INSERT INTO cloud_index(fid,pid,name,full_path,pickcode,sha1,size,is_dir,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(fid) DO UPDATE SET "
            "pid=excluded.pid,name=excluded.name,pickcode=excluded.pickcode,"
            "sha1=excluded.sha1,size=excluded.size,is_dir=excluded.is_dir,updated_at=excluded.updated_at",
            (it["fid"], it["cid"], it["name"], "", it["pickcode"], it["sha1"], it["size"],
             1 if it["is_dir"] else 0, time.time()))
        ext = Path(it["name"]).suffix.lower()
        if ext in pan115.VIDEO_EXT:
            if it["fid"] in known or not it["pickcode"]:
                stats["skip"] += 1
                continue
            local = strm.write_strm(sp["local_dir"], it["name"], it["pickcode"], rel=it["rel"])
            strm.record_strm(it["fid"], it["pickcode"], it["cid"], local, it["size"], it["sha1"])
            stats["video"] += 1
        elif ext in pan115.META_EXT and sp["with_meta"]:
            try:
                await _download_meta(it, sp["local_dir"])
                stats["meta"] += 1
            except Exception as e:
                log("WARN", "sync", f"元数据下载失败 {it['name']}: {type(e).__name__}: {e}")
        if stats["video"] % 200 == 199:
            d.commit()  # 大批量时分批提交，避免长事务
    d.commit()

    # 清理网盘已删除的（仅限本 sync_path 前缀）
    orphan = known - seen
    if orphan:
        rows = d.execute(
            f"SELECT pan_fid FROM strm_files WHERE pan_fid IN ({','.join('?' * len(orphan))})",
            tuple(orphan)).fetchall()
        prefix = str(Path(config.MEDIA_ROOT) / sp["local_dir"].lstrip("/"))
        for r in rows:
            row = d.execute("SELECT local_path FROM strm_files WHERE pan_fid=?", (r["pan_fid"],)).fetchone()
            if row and row["local_path"].startswith(prefix):
                strm.remove_strm(r["pan_fid"])
    d.execute("UPDATE sync_paths SET last_full_sync=? WHERE id=?", (time.time(), path_id))
    d.commit()
    event("full_sync_done", {"path": sp["name"], **stats})
    log("INFO", "sync", f"全量同步完成 {sp['name']}: 视频{stats['video']} 元数据{stats['meta']} 跳过{stats['skip']} 目录{stats['dir']}")
    return stats


async def _download_meta(item: dict, local_dir: str) -> None:
    """下载网盘小文件（nfo/字幕/图片）到本地。"""
    import httpx
    from ..core.pan115 import download_url
    url = await download_url(item["pickcode"])
    d = Path(config.MEDIA_ROOT) / local_dir.lstrip("/")
    rel = item.get("rel") or ""
    if rel:
        for seg in rel.split("/"):
            if seg:
                d = d / strm.safe_name(seg)
    d.mkdir(parents=True, exist_ok=True)
    target = d / strm.safe_name(item["name"])
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        r = await c.get(url)
        if r.status_code == 200:
            target.write_bytes(r.content)


async def incremental_sync() -> dict:
    """增量同步：消费 115 生活事件。
    结构：data.list 是按行为/日期分组的列表，每组 behavior_type + items(文件明细)。
    路由：item.parent_id 命中 sync_dirs 目录映射 → 在对应本地子目录生成 strm。
    去重：life_events 表按 item id。删除事件 115 不提供，由周期全量同步兜底清理。"""
    from ..config import get, set as cfg_set
    try:
        r = await pan115.life_events(limit=1000)
    except Exception as e:
        log("ERROR", "sync", f"生活事件拉取失败: {type(e).__name__}: {e}")
        return {"error": f"{type(e).__name__}: {e}"}
    data = r.get("data") or {}
    groups = data.get("list") or []

    d = db()
    # 目录路由表：pan_cid → (local_dir, rel)
    routes: dict[str, tuple[str, str]] = {}
    for row in d.execute(
            "SELECT sd.pan_cid, sd.rel, sp.local_dir FROM sync_dirs sd "
            "JOIN sync_paths sp ON sp.id=sd.sync_path_id WHERE sp.enabled=1").fetchall():
        routes[str(row["pan_cid"])] = (row["local_dir"], row["rel"])

    handled = 0
    total = 0
    cursor0 = int(get("pan115.life_cursor", "0") or 0)
    latest = cursor0
    for g in groups:
        g_time = 0
        try:
            g_time = int(g.get("update_time") or 0)
            latest = max(latest, g_time)
        except (TypeError, ValueError):
            pass
        # 组快照时间未超过游标 = 整组都是旧事件，直接跳过（item 级 life_events 去重兜底）
        if cursor0 and g_time and g_time <= cursor0:
            continue
        btype = str(g.get("behavior_type") or "")
        is_add = btype in _ADD_BEHAVIORS
        is_del = "delete" in btype or "del_" in btype
        if not (is_add or is_del):
            continue
        for it in g.get("items") or []:
            total += 1
            ev_id = str(it.get("id") or "")
            if not ev_id:
                continue
            if d.execute("SELECT 1 FROM life_events WHERE id=?", (ev_id,)).fetchone():
                continue
            name = it.get("file_name") or ""
            fid = str(it.get("file_id") or "")
            cid = str(it.get("parent_id") or "")
            pickcode = it.get("pick_code") or ""
            d.execute(
                "INSERT OR IGNORE INTO life_events(id,type,file_name,pan_cid,pan_fid,handled,created_at) "
                "VALUES(?,?,?,?,?,0,?)", (ev_id, btype, name, cid, fid, time.time()))
            route = routes.get(cid)
            ext = Path(name).suffix.lower()
            if is_del:
                if fid:
                    strm.remove_strm(fid)
                    handled += 1
            elif route and ext in pan115.VIDEO_EXT and pickcode:
                local_dir, rel = route
                local = strm.write_strm(local_dir, name, pickcode, rel=rel)
                old = d.execute("SELECT local_path FROM strm_files WHERE pan_fid=?", (fid,)).fetchone()
                if old and old["local_path"] != local:
                    try:
                        os.remove(old["local_path"])
                    except OSError:
                        pass
                strm.record_strm(fid, pickcode, cid, local, int(it.get("file_size") or 0), it.get("sha1") or "")
                handled += 1
                log("INFO", "sync", f"增量入库: {name} → {local}")
            elif ext in pan115.VIDEO_EXT and not route:
                log("INFO", "sync", f"事件未命中同步目录，跳过: {name} (cid={cid})")
            d.execute("UPDATE life_events SET handled=1 WHERE id=?", (ev_id,))
    d.commit()
    cfg_set("pan115.life_cursor", str(latest))
    if handled:
        event("incr_sync", {"handled": handled})
    return {"groups": len(groups), "items": total, "handled": handled, "cursor": latest}


async def cleanup_invalid() -> dict:
    """无效清理（Symedia 手动同步的清理选项）：
    本地存在但 115 云端已不存在的 STRM 删除 + 空目录回收。
    以云端索引（cloud_index，需先全量同步或接管 CMS 数据）为准。"""
    d = db()
    cloud_fids = {r["fid"] for r in d.execute("SELECT fid FROM cloud_index WHERE is_dir=0").fetchall()}
    stats = {"removed_strm": 0, "removed_dirs": 0, "checked": 0}
    rows = d.execute("SELECT pan_fid, local_path FROM strm_files").fetchall()
    for r in rows:
        stats["checked"] += 1
        if cloud_fids and r["pan_fid"] not in cloud_fids:
            strm.remove_strm(r["pan_fid"])
            stats["removed_strm"] += 1
    # 回收空目录（只扫我们管理的同步目录，从最深的开始）
    media_root = Path(config.MEDIA_ROOT)
    roots = {media_root / r["local_dir"].lstrip("/")
             for r in d.execute("SELECT local_dir FROM sync_paths").fetchall()}
    for root in roots:
        if not root.exists():
            continue
        dirs = sorted((p for p in root.rglob("*") if p.is_dir()),
                      key=lambda p: len(p.parts), reverse=True)
        for p in dirs:
            try:
                if not any(p.iterdir()):
                    p.rmdir()
                    stats["removed_dirs"] += 1
            except OSError:
                pass
    log("INFO", "sync", f"无效清理完成: {stats}")
    event("cleanup_done", stats)
    return stats
