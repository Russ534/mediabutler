"""聚影（jying.top）每日签到。接口对齐秋阳 jying-checkin.py 实测约定：
POST /api/key/v1/checkin/do/ + X-API-Key，必须浏览器 UA；
当天已签返回 HTTP 400 + {"checked_today": true}，视为成功。"""
from .. import config
from ..database import log
from .http import client, UA

URL = "https://www.jying.top/api/key/v1/checkin/do/"


async def checkin() -> str:
    key = config.get("jying.api_key")
    if not key:
        return "聚影签到跳过：未配置 jying.api_key"
    try:
        async with client(timeout=40) as c:
            r = await c.post(URL, headers={"X-API-Key": key, "User-Agent": UA,
                                           "Accept": "application/json"}, content=b"")
            if r.status_code == 200:
                return f"聚影签到成功: {r.text[:120]}"
            # 已签到也算成功
            try:
                d = r.json()
            except Exception:
                d = {}
            if d.get("checked_today"):
                return "聚影今日已签到"
            msg = f"聚影签到失败 HTTP {r.status_code}: {r.text[:150]}"
            log("WARN", "jying", msg)
            return msg
    except Exception as e:
        log("ERROR", "jying", f"聚影签到异常: {e}")
        return f"聚影签到异常: {e}"
