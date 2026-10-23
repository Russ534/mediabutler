"""Telegram 监控引擎（Telethon userbot）：
- 7x24 监听频道新消息 → 净化 → 解析 → 匹配订阅 → 推送
- 历史消息缓存（本地秒级搜索）
- TG 主动搜索（在指定群组/机器人中搜索关键词）
- TG 指令（搜 xxx / 签到）"""
import asyncio
import json
import time

from .. import config
from ..database import db, event, log

_client = None
_listener_task: asyncio.Task | None = None


def configured() -> bool:
    return bool(config.get("tg.api_id") and config.get("tg.api_hash"))


async def get_client():
    """获取已登录的 Telethon 客户端（未登录返回 None）。

    优先使用 tg.session_string（StringSession，免扫码）；
    否则回落到本地 session 文件（手机验证码登录后生成）。
    """
    global _client
    if _client and _client.is_connected():
        return _client
    if not configured():
        return None
    from telethon import TelegramClient
    c = TelegramClient(_session_spec(), int(config.get("tg.api_id")), config.get("tg.api_hash"),
                       proxy=_proxy())
    await c.connect()
    if await c.is_user_authorized():
        _client = c
        return c
    await c.disconnect()
    return None


def _session_spec():
    """session_string 优先，否则用本地 session 文件路径。"""
    s = config.get("tg.session_string")
    if s:
        from telethon.sessions import StringSession
        return StringSession(s)
    return config.TG_SESSION_PATH


def _proxy():
    p = config.get("tg.proxy")
    if not p:
        return None
    # socks5://[user:pass@]host:port
    import re
    m = re.match(r"socks5://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)", p)
    if m:
        return ("socks5", m.group(3), int(m.group(4)), True, m.group(1), m.group(2))
    return None


async def send_code(phone: str) -> str:
    """登录第一步：发验证码，返回 phone_code_hash。"""
    if not configured():
        raise RuntimeError("请先配置 TG api_id / api_hash")
    from telethon import TelegramClient
    global _client
    _client = TelegramClient(config.TG_SESSION_PATH, int(config.get("tg.api_id")), config.get("tg.api_hash"))
    await _client.connect()
    r = await _client.send_code_request(phone)
    return r.phone_code_hash


async def sign_in(phone: str, code: str, phone_code_hash: str, password: str = "") -> bool:
    """登录第二步：提交验证码（可选两步验证密码）。"""
    global _client
    if not _client:
        return False
    try:
        await _client.sign_in(phone, code, phone_code_hash=phone_code_hash)
    except Exception as e:
        if "password" in str(e).lower() and password:
            await _client.sign_in(password=password)
        else:
            raise
    log("INFO", "tg", "Telegram 登录成功")
    # 登录成功后把 session 字符串存下来，容器重建也能免验证恢复
    try:
        from telethon.sessions import StringSession
        config.set("tg.session_string", StringSession.save(_client.session))
    except Exception:
        pass
    return True


async def dialogs() -> list[dict]:
    """对话列表（频道/群组/机器人），供监控规则下拉选择。"""
    c = await get_client()
    if not c:
        return []
    out = []
    async for d in c.iter_dialogs(limit=300):
        out.append({"id": d.id, "name": d.name or "",
                    "type": "channel" if getattr(d.entity, "broadcast", False)
                    else "group" if getattr(d.entity, "megagroup", False)
                    else "bot" if getattr(d.entity, "bot", False) else "user"})
    return out


async def _handle_message(channel_id: int, message_id: int, text: str, msg_date: float) -> None:
    from ..core.parser import purify, extract_links, parse_title
    clean = purify(text)
    links = extract_links(clean)
    parsed = parse_title(clean[:300])
    d = db()
    d.execute(
        "INSERT INTO tg_cache(channel_id,message_id,text,links,parsed,msg_date) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(channel_id,message_id) DO UPDATE SET text=excluded.text,links=excluded.links,parsed=excluded.parsed",
        (channel_id, message_id, clean[:8000], json.dumps(links), json.dumps(parsed, ensure_ascii=False), msg_date))
    d.commit()
    if not links:
        return
    # 按监控规则匹配
    from .matcher import match_resource, push_to_target
    rules = d.execute("SELECT * FROM tg_rules WHERE enabled=1").fetchall()
    for rule in rules:
        ids = json.loads(rule["channel_ids"])
        if channel_id not in ids and str(channel_id) not in [str(i) for i in ids]:
            continue
        for link in links:
            for decision in match_resource(clean[:1000], link,
                                           only_missing=bool(rule["only_missing"]),
                                           extra_keywords=rule["keywords"]):
                ok = await push_to_target(decision, rule["push_target"], rule["forward_chat_id"])
                event("resource_hit", {"rule": rule["name"], "sub": decision["sub"]["title"],
                                       "target": rule["push_target"], "ok": ok})


async def start_listener() -> None:
    """启动 7x24 监听（注册了 tg_rules 的频道）。"""
    global _listener_task
    c = await get_client()
    if not c:
        log("WARN", "tg", "TG 未登录或未配置，监听未启动")
        return
    from telethon import events as tg_events

    d = db()
    channel_ids: set[int] = set()
    for r in d.execute("SELECT channel_ids FROM tg_rules WHERE enabled=1").fetchall():
        for i in json.loads(r["channel_ids"]):
            try:
                channel_ids.add(int(i))
            except (TypeError, ValueError):
                pass
    if not channel_ids:
        log("INFO", "tg", "暂无启用的监控规则，监听待命")
    log("INFO", "tg", f"TG 监听启动，覆盖 {len(channel_ids)} 个频道/群组")

    @c.on(tg_events.NewMessage)
    async def _on_msg(ev):
        try:
            cid = ev.chat_id
            if channel_ids and cid not in channel_ids and abs(cid) not in channel_ids:
                return
            text = ev.message.message or ""
            if not text:
                return
            await _handle_message(cid, ev.message.id, text, ev.message.date.timestamp())
        except Exception as e:
            log("ERROR", "tg", f"消息处理异常: {e}")

    # 指令 bot：给自己发「搜 xxx」触发主动搜索
    @c.on(tg_events.NewMessage(outgoing=True, pattern=r"^搜\s+(.+)$"))
    async def _on_cmd(ev):
        kw = ev.pattern_match.group(1).strip()
        results = await search_cached(kw, limit=5)
        lines = [f"🔍 「{kw}」缓存命中 {len(results)} 条"]
        for r in results:
            lines.append(f"· {r['text'][:80]}")
        await ev.reply("\n".join(lines) or "无结果")

    async def _run():
        await c.run_until_disconnected()

    _listener_task = asyncio.create_task(_run())


async def cache_history(channel_id: int, limit: int = 500) -> int:
    """拉取频道历史消息进本地缓存。"""
    c = await get_client()
    if not c:
        return 0
    n = 0
    async for msg in c.iter_messages(channel_id, limit=limit):
        if msg.message:
            await _handle_message(channel_id, msg.id, msg.message, msg.date.timestamp())
            n += 1
    log("INFO", "tg", f"频道 {channel_id} 历史缓存 {n} 条")
    return n


async def search_cached(keyword: str, limit: int = 20) -> list[dict]:
    d = db()
    rows = d.execute(
        "SELECT * FROM tg_cache WHERE text LIKE ? ORDER BY msg_date DESC LIMIT ?",
        (f"%{keyword}%", limit)).fetchall()
    return [dict(r) for r in rows]


async def tg_search(keyword: str, chat_ids: list[int], limit: int = 30) -> list[dict]:
    """TG 主动搜索：在指定群组/频道内搜索关键词，结果进缓存并返回。"""
    c = await get_client()
    if not c:
        return []
    found = []
    for cid in chat_ids:
        try:
            async for msg in c.iter_messages(cid, search=keyword, limit=limit):
                if msg.message:
                    await _handle_message(cid, msg.id, msg.message, msg.date.timestamp())
                    found.append({"channel_id": cid, "text": msg.message[:300], "id": msg.id})
        except Exception as e:
            log("WARN", "tg", f"TG 搜索 {cid} 失败: {e}")
    return found


async def stop_listener() -> None:
    global _listener_task, _client
    if _listener_task:
        _listener_task.cancel()
        _listener_task = None
    if _client:
        await _client.disconnect()
        _client = None
