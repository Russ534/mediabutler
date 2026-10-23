"""通知：Telegram Bot / 企业微信 / 自定义 Webhook。含 CineChill 风格富文本入库通知。"""
import datetime

from .. import config
from ..database import log
from .http import client


async def send(text: str, title: str = "MediaButler") -> bool:
    """向所有已配置渠道发送通知。任一成功即 True。"""
    ok = False
    # Telegram Bot
    token = config.get("tg.bot_token")
    chat_id = config.get("tg.notify_chat_id")
    if token and chat_id:
        try:
            async with client("tg.proxy", timeout=15) as c:
                r = await c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 json={"chat_id": chat_id, "text": f"【{title}】\n{text}",
                                       "disable_web_page_preview": True})
                ok = ok or r.status_code == 200
        except Exception as e:
            log("WARN", "notify", f"TG 通知失败: {e}")
    # 企业微信机器人
    wurl = config.get("notify.wecom_webhook")
    if wurl:
        try:
            async with client(timeout=15) as c:
                r = await c.post(wurl, json={"msgtype": "text", "text": {"content": f"【{title}】\n{text}"}})
                ok = ok or r.status_code == 200
        except Exception as e:
            log("WARN", "notify", f"企业微信通知失败: {e}")
    # 自定义 webhook
    curl_ = config.get("notify.webhook")
    if curl_:
        try:
            async with client(timeout=15) as c:
                r = await c.post(curl_, json={"title": title, "text": text})
                ok = ok or r.status_code == 200
        except Exception as e:
            log("WARN", "notify", f"Webhook 通知失败: {e}")
    return ok


async def media_added(sub: dict, action: str = "入库") -> bool:
    """CineChill 风格富文本入库通知：评分/类型/首播/集数/缺集/简介/TMDB 链接。"""
    title = sub.get("title") or ""
    year = sub.get("year") or ""
    mtype = "剧集" if sub.get("media_type") == "tv" else "电影"
    tmdb_id = sub.get("tmdb_id")
    lines = [f"⭐️《{title}》{f' ({year})' if year else ''} {action} ✅"]
    detail: dict = {}
    if tmdb_id and config.get("tmdb.api_key"):
        try:
            from . import tmdb
            detail = await tmdb.detail(sub.get("media_type") or "movie", int(tmdb_id))
        except Exception:
            detail = {}
    if detail:
        rating = detail.get("vote_average")
        if rating:
            lines.append(f"评分：{rating:.1f}")
        genres = " / ".join(g["name"] for g in (detail.get("genres") or [])[:4])
        if genres:
            lines.append(f"类型：{mtype} · {genres}")
        air = detail.get("first_air_date") or detail.get("release_date")
        if air:
            lines.append(f"{'首播' if mtype == '剧集' else '上映'}：{air}")
        if mtype == "剧集" and detail.get("number_of_episodes"):
            lines.append(f"集数：共 {detail['number_of_episodes']} 集")
        ov = (detail.get("overview") or "").strip()
        if ov:
            lines.append(f"简介：{ov[:120]}{'…' if len(ov) > 120 else ''}")
        lines.append(f"TMDB：https://www.themoviedb.org/{sub.get('media_type')}/{tmdb_id}")
    missing = sub.get("missing")
    if mtype == "剧集" and missing and missing != "[]":
        lines.append(f"缺集：{missing}")
    lines.append("时间：" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    return await send("\n".join(lines), "入库通知")
