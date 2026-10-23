"""115 网盘封装（基于 p115client）：登录校验、目录列表、生活事件、转存、离线、签到、直链。"""
from functools import lru_cache

from .. import config
from ..database import log

VIDEO_EXT = {".mp4", ".mkv", ".avi", ".ts", ".m2ts", ".iso", ".rmvb", ".wmv", ".flv", ".mov", ".mpg", ".vob"}
META_EXT = {".nfo", ".srt", ".ass", ".ssa", ".sub", ".vtt", ".jpg", ".jpeg", ".png", ".webp", ".ttf", ".otf"}


def _client():
    from p115client import P115Client
    cookie = config.get("pan115.cookie")
    if not cookie:
        raise RuntimeError("未配置 115 Cookie")
    return P115Client(cookie, console_qrcode=False)


async def test() -> dict:
    c = _client()
    info = await c.user_info(async_=True)
    data = info.get("data") or info
    return {"name": data.get("uname") or data.get("name") or "?",
            "vip": data.get("is_vip") or data.get("vip"),
            "raw_state": info.get("state", True)}


async def space_info() -> dict:
    c = _client()
    return await c.fs_storage_info(async_=True)


async def list_dir(cid: str | int = 0, limit: int = 1000, offset: int = 0, asc: int = 0) -> dict:
    """列出目录：返回 {count, data:[{fid,fn,pc(选取码),s(size),fc(是否目录)...}]}"""
    c = _client()
    payload = {"cid": str(cid), "limit": limit, "offset": offset, "asc": asc,
               "o": "file_name", "show_dir": 1, "stdril": 1}
    return await c.fs_files(payload, async_=True)


async def walk(cid: str | int, _depth: int = 0, _max_depth: int = 12, _rel: str = ""):
    """递归遍历目录，yield 文件/目录条目（dict）。目录条目含 'is_dir'=True。
    每个条目带 'rel'：相对遍历起点的所在目录路径（不含自身文件名）。"""
    offset = 0
    while True:
        try:
            r = await list_dir(cid, limit=1150, offset=offset)
        except Exception as e:
            log("ERROR", "115", f"列目录失败 cid={cid}: {e}")
            return
        items = r.get("data") or []
        if not items:
            return
        for it in items:
            fid = str(it.get("fid") or it.get("cid") or "")
            # p115 fs_files: 目录没有 pc 字段
            is_dir = not it.get("pc")
            name = it.get("n") or it.get("fn") or ""
            entry = {"fid": fid, "name": name, "size": int(it.get("s") or 0),
                     "pickcode": it.get("pc") or "", "sha1": it.get("sha") or it.get("sha1") or "",
                     "cid": str(cid), "is_dir": is_dir, "rel": _rel}
            yield entry
            if is_dir and _depth < _max_depth:
                async for sub in walk(fid, _depth + 1, _max_depth, f"{_rel}/{name}" if _rel else name):
                    yield sub
        if len(items) < 1150:
            return
        offset += 1150


async def mkdir(pid: str | int, name: str) -> str:
    c = _client()
    r = await c.fs_mkdir({"cname": name}, pid=pid, async_=True)
    return str((r.get("data") or {}).get("cid") or r.get("cid") or "")


async def ensure_dir(path_parts: list[str], root: str | int = 0) -> str:
    """逐级确保目录存在，返回末级 cid。"""
    c = _client()
    cid = str(root)
    for part in path_parts:
        nid = ""
        offset = 0
        while True:
            r = await list_dir(cid, limit=1150, offset=offset)
            for it in (r.get("data") or []):
                if (it.get("n") or "") == part and not it.get("pc"):
                    nid = str(it.get("cid") or it.get("fid"))
                    break
            if nid or len(r.get("data") or []) < 1150:
                break
            offset += 1150
        if not nid:
            nid = await mkdir(cid, part)
        cid = nid
    return cid


async def move(file_ids: list[str], pid: str | int) -> dict:
    c = _client()
    return await c.fs_move(file_ids, pid=pid, async_=True)


async def rename(fid: str | int, new_name: str) -> dict:
    c = _client()
    return await c.fs_rename((fid, new_name), async_=True)


async def dir_getid(path: str) -> str:
    c = _client()
    r = await c.fs_dir_getid({"path": path}, async_=True)
    return str((r.get("data") or {}).get("id") or r.get("id") or "0")


async def life_events(limit: int = 1000) -> dict:
    """115 生活事件（增量同步数据源）。show_type=1 只要文件系统操作（增删改/移动/上传/接收），
    过滤掉 account_security 等无关事件。返回 data.list 为「按行为/日期分组」的列表，
    每组含 behavior_type 与 items（文件明细）。轮询场景取第一页 + DB 去重即可。"""
    c = _client()
    return await c.life_list({"limit": limit, "show_type": 1}, async_=True)


async def share_receive(share_url: str, target_cid: str | int = 0) -> dict:
    """接收分享链接转存到自己的网盘。"""
    c = _client()
    return await c.share_receive({"share_url": share_url, "cid": str(target_cid)},
                                 share_url=share_url, async_=True)


async def add_offline(urls: list[str], target_cid: str | int | None = None) -> dict:
    """磁力/电驴离线下载。"""
    c = _client()
    payload: dict = {"urls": "\n".join(urls)}
    if target_cid is not None:
        payload["wp_path_id"] = str(target_cid)
    return await c.clouddownload_task_add_urls(payload, async_=True)


async def checkin() -> dict:
    """115 每日签到。"""
    c = _client()
    return await c.user_points_sign_post(async_=True)


async def download_url(pickcode: str, ua: str | None = None) -> str:
    """pickcode → 115 CDN 直链。"""
    c = _client()
    u = await c.download_url(pickcode, user_agent=ua, async_=True)
    return str(u)
