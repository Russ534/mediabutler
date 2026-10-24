"""SQLite 存储层：连接、schema、日志/事件助手。"""
import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import DB_PATH

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS subscriptions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  media_type TEXT NOT NULL,            -- movie | tv
  tmdb_id INTEGER,
  douban_id TEXT,
  title TEXT NOT NULL,
  year TEXT,
  poster TEXT,
  season INTEGER,                      -- 仅剧集：订阅某一季，NULL=全部
  status TEXT NOT NULL DEFAULT 'missing',  -- missing|partial|in_library|washing|archived
  missing TEXT NOT NULL DEFAULT '[]',  -- JSON: 缺集列表 [1,2,3]
  total_episodes INTEGER DEFAULT 0,
  wash_days INTEGER DEFAULT 0,         -- 洗版天数，0=不洗版
  wash_until TEXT,
  keywords TEXT DEFAULT '',            -- 额外关键词过滤（支持 /正则/）
  source TEXT DEFAULT 'manual',        -- manual|douban|emby|tg
  note TEXT DEFAULT '',
  last_search_at REAL DEFAULT 0,        -- v2.0.2: 盘搜轮询游标
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS resources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sub_id INTEGER,
  title TEXT,
  link TEXT,
  link_type TEXT,                      -- 115|magnet|ed2k|quark|...
  share_code TEXT,
  source TEXT,                         -- tg频道名|pansou|hdhive
  resolution TEXT,
  size TEXT,
  season INTEGER, episodes TEXT DEFAULT '[]',
  status TEXT DEFAULT 'new',           -- new|pushed|ignored|failed
  push_target TEXT DEFAULT '',
  message TEXT DEFAULT '',
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_resources_sub ON resources(sub_id);
CREATE TABLE IF NOT EXISTS tg_cache(
  channel_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL,
  text TEXT,
  links TEXT DEFAULT '[]',
  parsed TEXT DEFAULT '{}',
  msg_date REAL,
  PRIMARY KEY(channel_id, message_id)
);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  job_type TEXT NOT NULL,              -- douban|emby_sync|pan_search|checkin115|checkin_hdhive|tg_search|strm_sync|incr_sync|missing_scan|organize
  cron TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  config TEXT DEFAULT '{}',
  last_run REAL, last_result TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sync_paths(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  pan_cid TEXT NOT NULL DEFAULT '0',   -- 115 目录 cid
  pan_path TEXT DEFAULT '',
  local_dir TEXT NOT NULL,             -- /media 下本地目录
  media_type TEXT DEFAULT 'tv',        -- movie|tv|mixed
  with_meta INTEGER DEFAULT 1,         -- 下载 nfo/图片/字幕
  enabled INTEGER DEFAULT 1,
  last_full_sync REAL,
  UNIQUE(pan_cid, local_dir)
);
CREATE TABLE IF NOT EXISTS strm_files(
  pan_fid TEXT PRIMARY KEY,            -- 115 文件 id
  pickcode TEXT,
  pan_cid TEXT,
  local_path TEXT,
  size INTEGER, sha1 TEXT,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS life_events(
  id TEXT PRIMARY KEY,                 -- 115 生活事件 id
  type TEXT, file_name TEXT, pan_cid TEXT, pan_fid TEXT,
  handled INTEGER DEFAULT 0,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS sync_dirs(
  sync_path_id INTEGER NOT NULL,       -- 所属同步路径
  pan_cid TEXT NOT NULL,               -- 115 目录 cid
  rel TEXT NOT NULL DEFAULT '',        -- 相对同步根的目录路径（不含文件名）
  PRIMARY KEY(sync_path_id, pan_cid)
);
CREATE TABLE IF NOT EXISTS organize_rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  watch_cid TEXT NOT NULL,             -- 监看的 115 目录（如"待整理"）
  match_keywords TEXT DEFAULT '',
  media_type TEXT DEFAULT 'auto',
  target_cid TEXT NOT NULL,            -- 目标目录 cid
  rename_template TEXT DEFAULT '{title} ({year})/{title} ({year}){ext}',
  enabled INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS tg_rules(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  channel_ids TEXT NOT NULL DEFAULT '[]',  -- JSON 数组
  push_target TEXT DEFAULT 'pan115',       -- pan115|tg|none
  forward_chat_id TEXT DEFAULT '',
  only_missing INTEGER DEFAULT 1,
  keywords TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT, module TEXT, message TEXT, created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(created_at);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT, payload TEXT DEFAULT '{}', created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);
-- 115 云端文件索引（全量同步/接管 CMS cloud_data 后作为「云端真相」，用于无效清理与增量基线）
CREATE TABLE IF NOT EXISTS cloud_index(
  fid TEXT PRIMARY KEY,
  pid TEXT DEFAULT '',
  name TEXT,
  full_path TEXT,                  -- 网盘内完整路径 /Media/Video/...
  pickcode TEXT DEFAULT '',
  sha1 TEXT DEFAULT '',
  size INTEGER DEFAULT 0,
  is_dir INTEGER DEFAULT 0,
  updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_cloud_pid ON cloud_index(pid);
CREATE INDEX IF NOT EXISTS idx_cloud_pick ON cloud_index(pickcode);
-- 接管迁移记录
CREATE TABLE IF NOT EXISTS legacy_imports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT, kind TEXT, detail TEXT DEFAULT '{}', created_at REAL
);
"""


def db() -> sqlite3.Connection:
    con = getattr(_local, "con", None)
    if con is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=120)
        con.execute("PRAGMA busy_timeout=120000")
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        # 每个连接的页缓存封顶 32MB（thread-local 多连接场景防内存无界增长）
        con.execute("PRAGMA cache_size=-32768")
        con.executescript(SCHEMA)
        # 老库迁移：补新增列（已存在则忽略）
        for mig in ("ALTER TABLE subscriptions ADD COLUMN last_search_at REAL DEFAULT 0",):
            try:
                con.execute(mig)
            except sqlite3.OperationalError:
                pass
        _local.con = con
    return con


_lock = threading.Lock()


_log_calls = 0


def log(level: str, module: str, message: str) -> None:
    global _log_calls
    with _lock:
        d = db()
        d.execute("INSERT INTO logs(level,module,message,created_at) VALUES(?,?,?,?)",
                  (level, module, message[:4000], time.time()))
        _log_calls += 1
        # 清理旧日志：每 200 次插入才跑一次（原来每条都跑 ORDER BY 子查询，纯浪费）
        if _log_calls % 200 == 0:
            d.execute("DELETE FROM logs WHERE id < (SELECT MIN(id) FROM (SELECT id FROM logs ORDER BY id DESC LIMIT 20000))")
        d.commit()
    print(f"[{level}] {module}: {message}", flush=True)


def event(type_: str, payload: dict) -> None:
    with _lock:
        d = db()
        d.execute("INSERT INTO events(type,payload,created_at) VALUES(?,?,?)",
                  (type_, json.dumps(payload, ensure_ascii=False)[:4000], time.time()))
        d.execute("DELETE FROM events WHERE id < (SELECT MIN(id) FROM (SELECT id FROM events ORDER BY id DESC LIMIT 5000))")
        d.commit()
