"""通知：Telegram Bot / 企业微信 / 自定义 Webhook。"""
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
                                 json={"chat_id": chat_id, "text": f"【{title}】\n{text}"})
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
