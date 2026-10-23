"""STRM 生成与元数据落地（复刻 CMS 全量同步 + CineChill STRM 网关思路）。"""
import os
import re
from pathlib import Path

from .. import config
from ..database import db, log

_illegal = re.compile(r'[\\/:*?"<>|]')


def safe_name(name: str) -> str:
    return _illegal.sub("_", name).strip() or "unnamed"


def strm_url(pickcode: str, filename: str) -> str:
    host = config.STRM_HOST or f"http://127.0.0.1:{config.WEB_PORT}"
    return f"{host.rstrip('/')}/stream/{pickcode}/{safe_name(filename)}"


def write_strm(local_dir: str, filename: str, pickcode: str, rel: str = "") -> str:
    """写 .strm 文件，返回本地路径。rel 为相对 local_dir 的子目录（还原网盘层级）。"""
    d = Path(config.MEDIA_ROOT) / local_dir.lstrip("/")
    if rel:
        for seg in rel.split("/"):
            if seg:
                d = d / safe_name(seg)
    d.mkdir(parents=True, exist_ok=True)
    stem = safe_name(Path(filename).stem)
    p = d / f"{stem}.strm"
    p.write_text(strm_url(pickcode, filename), encoding="utf-8")
    return str(p)


def record_strm(pan_fid: str, pickcode: str, pan_cid: str, local_path: str, size: int, sha1: str) -> None:
    import time
    d = db()
    d.execute(
        "INSERT INTO strm_files(pan_fid,pickcode,pan_cid,local_path,size,sha1,updated_at) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(pan_fid) DO UPDATE SET pickcode=excluded.pickcode,pan_cid=excluded.pan_cid,"
        "local_path=excluded.local_path,size=excluded.size,sha1=excluded.sha1,updated_at=excluded.updated_at",
        (pan_fid, pickcode, pan_cid, local_path, size, sha1, time.time()))
    d.commit()


def remove_strm(pan_fid: str) -> None:
    d = db()
    row = d.execute("SELECT local_path FROM strm_files WHERE pan_fid=?", (pan_fid,)).fetchone()
    if row:
        try:
            os.remove(row["local_path"])
        except OSError:
            pass
        d.execute("DELETE FROM strm_files WHERE pan_fid=?", (pan_fid,))
        d.commit()
        log("INFO", "strm", f"删除失效 STRM: {row['local_path']}")


def known_fids() -> set[str]:
    return {r["pan_fid"] for r in db().execute("SELECT pan_fid FROM strm_files").fetchall()}
