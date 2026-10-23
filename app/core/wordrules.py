"""识别词表（融合 CMS words_replace.txt 与 Symedia 自定义识别词语法）。

规则文件：<config>/word_rules.txt，一行一条：
  # 注释
  屏蔽词                          —— 单个词，直接从标题里删除
  被替换词 => 替换词               —— 普通替换
  正则 => tmdb=12345               —— CMS 风格：命中后直接指定 TMDB ID
  正则 => {[tmdbid=45790;type=tv;s=1;e=1]}      —— Symedia 风格直指定
  正则(捕获) => {[tmdbid=248947;type=tv;s=1;e=\\1@*2+1]}  —— 捕获组集数偏移
  第 <> 集 >> EP+1                 —— 集数定位偏移（S01E <> 2160p >> 2*EP+1）
"""
import re
import time
from pathlib import Path

from .. import config
from ..database import log

RULES_PATH = config.BASE_DIR / "word_rules.txt"

_block: list[str] = []            # 屏蔽词
_replace: list[tuple] = []        # (compiled_re, replacement_template)
_ep_shift: list[tuple] = []       # (pre_word, post_word, expr)
_loaded_at = 0.0

RE_DIRECT = re.compile(r"^tmdb=(\d+)$")
RE_BRACE = re.compile(r"^\{\[(.+)\]\}$")
RE_SHIFT = re.compile(r"^(.+?)\s*<>\s*(.+?)\s*>>\s*(.+)$")


def _calc_ep(expr: str, ep: int) -> int:
    """对捕获的集数做 + - * / 偏移计算，如 2*EP+1。"""
    e = expr.replace("EP", str(ep)).replace("\\1", str(ep))
    if not re.fullmatch(r"[\d+\-*/ ().]+", e):
        return ep
    try:
        return int(eval(e, {"__builtins__": {}}, {}))
    except Exception:
        return ep


def reload(force: bool = False) -> None:
    global _loaded_at
    try:
        mtime = RULES_PATH.stat().st_mtime
    except OSError:
        return
    if not force and mtime <= _loaded_at:
        return
    _block.clear(); _replace.clear(); _ep_shift.clear()
    for raw in RULES_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = RE_SHIFT.match(line)
        if m and "=>" not in line:
            _ep_shift.append((m.group(1).strip(), m.group(2).strip(), m.group(3).strip()))
            continue
        if " => " in line:
            left, right = line.split(" => ", 1)
            try:
                _replace.append((re.compile(left.strip()), right.strip()))
            except re.error as e:
                log("WARN", "wordrules", f"规则正则不合法: {left} ({e})")
            continue
        _block.append(line)
    _loaded_at = mtime
    log("INFO", "wordrules", f"识别词表已加载: 屏蔽{len(_block)} 替换{len(_replace)} 偏移{len(_ep_shift)}")


def apply(title: str) -> dict:
    """应用词表。返回 {title, tmdb_id, media_type, season, episode}（后四项可空）。"""
    reload()
    out = {"title": title, "tmdb_id": None, "media_type": None, "season": None, "episode": None}
    t = title
    for w in _block:
        t = t.replace(w, " ")
    for rx, rep in _replace:
        m = rx.search(t)
        if not m:
            continue
        dm = RE_DIRECT.match(rep)
        bm = RE_BRACE.match(rep)
        if dm:
            out["tmdb_id"] = int(dm.group(1))
            t = rx.sub(" ", t)
        elif bm:
            fields = dict(p.split("=", 1) for p in bm.group(1).split(";") if "=" in p)
            if fields.get("tmdbid"):
                out["tmdb_id"] = int(fields["tmdbid"])
            if fields.get("type") in ("movie", "tv"):
                out["media_type"] = fields["type"]
            if fields.get("s"):
                out["season"] = int(fields["s"])
            if fields.get("e"):
                ep_expr = fields["e"]
                captured = int(m.group(1)) if m.groups() and m.group(1).isdigit() else 0
                if "\\1" in ep_expr or "@" in ep_expr:
                    base, _, calc = ep_expr.partition("@")
                    ep_val = int(base.replace("\\1", str(captured)) or captured)
                    out["episode"] = _calc_ep(calc, ep_val) if calc else ep_val
                else:
                    out["episode"] = int(ep_expr)
            t = rx.sub(" ", t)
        else:
            t = rx.sub(rep, t)
    for pre, post, expr in _ep_shift:
        m = re.search(re.escape(pre) + r"\s*(\d{1,4})\s*" + re.escape(post), t)
        if m:
            out["episode"] = _calc_ep(expr, int(m.group(1)))
    out["title"] = re.sub(r"\s{2,}", " ", t).strip()
    return out
