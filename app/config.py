"""MediaButler 全局配置：路径、环境变量、设置存取（SQLite KV）。"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(os.environ.get("MB_CONFIG_DIR", "/config"))
BASE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE_DIR / "mediabutler.db"
TG_SESSION_PATH = str(BASE_DIR / "tg_session")
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
CACHE_DIR = BASE_DIR / "cache"
(CACHE_DIR / "emby_images").mkdir(parents=True, exist_ok=True)

WEB_PORT = int(os.environ.get("MB_PORT", "8866"))
MEDIA_ROOT = os.environ.get("MB_MEDIA_ROOT", "/media")
# STRM 文件里写入的网关地址（Emby 能访问到的本程序地址），如 http://192.168.1.10:8788
STRM_HOST = os.environ.get("MB_STRM_HOST", "")
TZ = os.environ.get("TZ", "Asia/Shanghai")

_DEFAULTS = {
    # emby
    "emby.host": "",
    "emby.api_key": "",
    # tmdb
    "tmdb.api_key": "",
    "tmdb.proxy": "",
    # 豆瓣
    "douban.cookie": "",
    "douban.user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "douban.user_id": "",
    # 115
    "pan115.cookie": "",
    # 影巢 hdhive
    "hdhive.api_key": "",
    "hdhive.gambler": "false",
    "hdhive.enabled": "true",
    # 盘搜（pansou 聚合，默认千山实例；可配第二源）
    "pansou.base_url": "https://pansou.qianshan.app",
    "pansou.base_url2": "",
    # AI（OpenAI 兼容接口，默认 wb-hub 反代；留空 api_key 则尝试 Gemini）
    "ai.base_url": "http://192.168.31.201:8788/v1",
    "ai.api_key": "",
    "ai.model": "deepseek-v4.1-flash",
    # 聚影 jying.top
    "jying.api_key": "",
    # telegram
    "tg.api_id": "",
    "tg.api_hash": "",
    "tg.bot_token": "",
    "tg.notify_chat_id": "",
    "tg.proxy": "",
    # gemini
    "gemini.api_key": "",
    "gemini.model": "gemini-2.0-flash",
    # 通知
    "notify.wecom_webhook": "",
    "notify.webhook": "",
    # 网络代理（全局兜底）
    "net.proxy": "",
}


def get(key: str, default: str = "") -> str:
    from .database import db
    row = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is not None:
        return row["value"]
    return _DEFAULTS.get(key, default)


def set(key: str, value: str) -> None:  # noqa: A001
    from .database import db
    db().execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    db().commit()


def all_settings(mask_secret: bool = True) -> dict:
    from .database import db
    rows = db().execute("SELECT key,value FROM settings").fetchall()
    data = dict(_DEFAULTS)
    data.update({r["key"]: r["value"] for r in rows})
    if mask_secret:
        for k in list(data):
            if any(s in k for s in ("cookie", "api_key", "token", "hash", "password")) and data[k]:
                v = data[k]
                data[k] = v[:6] + "****" + v[-4:] if len(v) > 12 else "****"
    return data


def ensure_admin_password() -> str | None:
    """首次启动生成随机密码并打印到日志；返回是否新生成。"""
    from .database import db
    row = db().execute("SELECT value FROM settings WHERE key='auth.password_hash'").fetchone()
    if row:
        return None
    import hashlib
    pwd = secrets.token_urlsafe(9)
    salt = secrets.token_hex(8)
    h = hashlib.sha256((salt + pwd).encode()).hexdigest()
    set("auth.salt", salt)
    set("auth.password_hash", h)
    set("auth.username", "admin")
    return pwd


def check_password(username: str, password: str) -> bool:
    import hashlib
    if username != get("auth.username", "admin"):
        return False
    salt = get("auth.salt")
    h = get("auth.password_hash")
    if not salt or not h:
        return False
    return hashlib.sha256((salt + password).encode()).hexdigest() == h
