"""资源匹配决策引擎：仅匹配缺集、关键词过滤、洗版质量判断（复刻 Media Automator 决策链）。"""
import json
import re

from ..database import db, log
from ..core.parser import parse_title


def _kw_match(keywords: str, text: str) -> bool:
    """关键词过滤：逗号分隔为 OR；/xxx/ 为正则。空=通过。"""
    keywords = (keywords or "").strip()
    if not keywords:
        return True
    for kw in re.split(r"[,，]", keywords):
        kw = kw.strip()
        if not kw:
            continue
        if kw.startswith("/") and kw.endswith("/"):
            try:
                if re.search(kw[1:-1], text, re.I):
                    return True
                continue
            except re.error:
                continue
        if kw.lower() in text.lower():
            return True
    return False


QUALITY_RANK = {"480P": 1, "720P": 2, "1080P": 3, "2K": 4, "4K": 5, "8K": 6}


def match_resource(text: str, link: str, only_missing: bool = True, extra_keywords: str = "") -> list[dict]:
    """拿一条资源消息与所有订阅匹配，返回决策列表 [{sub, parsed, reason, action}]。"""
    parsed = parse_title(text)
    d = db()
    subs = d.execute(
        "SELECT * FROM subscriptions WHERE status IN ('missing','partial','washing')").fetchall()
    decisions = []
    text_l = text.lower()
    for sub in subs:
        # 1. 标题相关性粗筛：订阅标题至少有一个>=2字的词出现在资源标题中
        title_words = [w for w in re.split(r"\s+", sub["title"]) if len(w) >= 2]
        if title_words and not any(w.lower() in text_l for w in title_words):
            continue
        # 2. 订阅级关键词过滤
        if not _kw_match(sub["keywords"], text):
            continue
        # 3. 规则级额外关键词
        if not _kw_match(extra_keywords, text):
            continue
        # 4. 季过滤
        if sub["media_type"] == "tv" and sub["season"] is not None and parsed["season"] is not None:
            if parsed["season"] != sub["season"]:
                continue
        # 5. 仅匹配缺集
        missing = json.loads(sub["missing"] or "[]")
        if sub["status"] == "washing":
            # 洗版期：只收更高质量
            cur_rank = 0  # 简化：洗版期收 4K/Remux 关键词
            if not re.search(r"(4K|2160P|REMUX|BluRay)", text, re.I):
                continue
            action = "wash"
        elif only_missing and sub["media_type"] == "tv" and parsed["episodes"] and missing:
            if not set(parsed["episodes"]) & set(missing):
                continue
            action = "fill"
        else:
            action = "fill"
        decisions.append({"sub": dict(sub), "parsed": parsed, "action": action, "link": link})
    return decisions


def record_resource(sub_id: int, title: str, link: str, source: str, parsed: dict,
                    status: str, push_target: str = "", message: str = "") -> None:
    import time
    from ..core.nullbr import _link_type
    d = db()
    d.execute(
        "INSERT INTO resources(sub_id,title,link,link_type,source,resolution,season,episodes,status,push_target,message,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (sub_id, title, link, _link_type(link), source, parsed.get("resolution", ""),
         parsed.get("season"), json.dumps(parsed.get("episodes", [])), status, push_target, message, time.time()))
    d.commit()


async def push_to_target(decision: dict, target: str, forward_chat_id: str = "") -> bool:
    """按推送目标执行：115 转存/离线、TG 转发。"""
    from ..core import pan115
    link = decision["link"]
    sub = decision["sub"]
    ok = False
    msg = ""
    try:
        if target == "pan115":
            from ..core.nullbr import _link_type
            lt = _link_type(link)
            if lt == "115":
                r = await pan115.share_receive(link)
                ok = bool(r.get("state", True))
                msg = str(r.get("error") or r.get("msg") or "")[:200]
            elif lt in ("magnet", "ed2k"):
                r = await pan115.add_offline([link])
                ok = bool(r.get("state", True))
                msg = str(r.get("error") or r.get("msg") or "")[:200]
            else:
                msg = f"暂不支持的链接类型 {lt}"
        elif target == "tg":
            from ..core.notify import send
            ok = await send(f"《{sub['title']}》命中资源\n{decision['parsed']['raw'][:200]}\n{link}", "资源推送")
        else:
            ok = True  # none：只匹配不推送
    except Exception as e:
        msg = str(e)[:200]
        log("ERROR", "matcher", f"推送失败: {e}")
    record_resource(sub["id"], decision["parsed"]["raw"][:300], link,
                    "tg" if target != "pan115" else "pan115", decision["parsed"],
                    "pushed" if ok else "failed", target, msg)
    if ok:
        log("INFO", "matcher", f"《{sub['title']}》资源已推送→{target}: {decision['parsed']['raw'][:60]}")
    return ok
