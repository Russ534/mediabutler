"""资源标题解析器：本地正则（高速）+ Gemini AI（兜底）。
从混乱的 TG/盘搜标题中提取 标题/年份/季/集/分辨率/编码/来源。"""
import json
import re

from .. import config
from ..database import log

RE_SEASON_EP = re.compile(
    r"[Ss](\d{1,2})\s*[Ee][Pp]?(\d{1,3})(?:\s*[-–~]\s*[Ee]?(\d{1,3}))?")
RE_SEASON_ONLY = re.compile(r"[Ss](\d{1,2})(?!\s*[Ee])|第\s*(\d{1,2})\s*季")
RE_EP_ONLY = re.compile(r"[Ee][Pp]?(\d{1,3})|第\s*(\d{1,3})\s*[集话]")
RE_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
RE_RES = re.compile(r"(2160[Pp]|4[Kk]|1080[Pp]|720[Pp]|480[Pp]|8[Kk]|2[Kk])")
RE_CODEC = re.compile(r"(HEVC|H\.?265|H\.?264|X265|X264|AV1|VC-?1|MPEG2?)", re.I)
RE_SOURCE = re.compile(r"(REMUX|BluRay|BDRip|WEB-?DL|WEBRip|HDTV|DVDRip|ISO)", re.I)
RE_EDITION = re.compile(r"(导演剪辑|Extended|Uncut|IMAX|剧场版|SP|OVA|特别篇)", re.I)

# 链接提取
RE_LINK = re.compile(
    r"(magnet:\?xt=urn:btih:[a-zA-Z0-9]{20,}[^\s]*"
    r"|ed2k://\|file\|[^\s]+\|"
    r"|https?://(?:115\.com|anxia\.com|115cdn\.com)[^\s<>'\")]+"
    r"|https?://(?:pan\.quark\.cn|pan\.baidu\.com|www\.alipan\.com|www\.aliyundrive\.com|www\.123pan\.com|123684\.com|123865\.com)[^\s<>'\")]+)")

# 常见广告/垃圾行
RE_AD = re.compile(r"(订阅|关注|加入|频道|群组|t\.me/joinchat|广告|推广|招代理|联系|客服|官方)")
# 文件大小信息（Symedia 识别细节：过滤大小提高准确率）
RE_SIZE = re.compile(r"[\[(\s]?\d+(?:\.\d+)?\s*(?:GB|MB|TB|GiB|MiB)[\])\s]?", re.I)
# 数字前缀（如「01.雷神2」「02 - xxx」，二次识别时去除）
RE_NUM_PREFIX = re.compile(r"^\s*\d{1,3}\s*[.、\-]\s*")


def parse_title(title: str) -> dict:
    """本地正则解析。先应用识别词表（屏蔽/替换/tmdb直指定/集数偏移）。"""
    from . import wordrules
    wr = wordrules.apply(title)
    title = wr["title"]
    title = RE_SIZE.sub(" ", title)
    r: dict = {"raw": title, "season": wr["season"], "episodes": [], "resolution": "",
               "codec": "", "source": "", "year": "", "title_guess": "",
               "tmdb_id": wr["tmdb_id"], "media_type": wr["media_type"]}
    m = RE_SEASON_EP.search(title)
    if m:
        r["season"] = int(m.group(1))
        e1 = int(m.group(2))
        e2 = int(m.group(3)) if m.group(3) else e1
        r["episodes"] = list(range(min(e1, e2), max(e1, e2) + 1))
    else:
        m = RE_SEASON_ONLY.search(title)
        if m and r["season"] is None:
            r["season"] = int(m.group(1) or m.group(2))
        m = RE_EP_ONLY.search(title)
        if m and not r["episodes"]:
            r["episodes"] = [int(m.group(1) or m.group(2))]
    if wr["episode"] is not None:
        r["episodes"] = [wr["episode"]]
    m = RE_RES.search(title)
    if m:
        r["resolution"] = m.group(1).upper().replace("2160P", "4K")
    m = RE_CODEC.search(title)
    if m:
        r["codec"] = m.group(1).upper().replace(".", "")
    m = RE_SOURCE.search(title)
    if m:
        r["source"] = m.group(1)
    m = RE_YEAR.search(title)
    if m:
        r["year"] = m.group(1)
    # 粗猜标题：去掉方括号标签后取第一段；数字前缀在二次猜测时剥掉
    clean = re.sub(r"[\[\]【】()（）]", " ", title)
    clean = RE_SEASON_EP.sub(" ", clean)
    clean = RE_SEASON_ONLY.sub(" ", clean)
    clean = RE_RES.sub(" ", clean)
    parts = [p.strip() for p in re.split(r"\s{2,}|[.·|｜]", clean) if len(p.strip()) >= 2]
    guess = parts[0][:40] if parts else title[:40]
    stripped = RE_NUM_PREFIX.sub("", guess).strip()
    r["title_guess"] = stripped or guess
    return r


def extract_links(text: str) -> list[str]:
    return [m.group(0).rstrip(".,;)") for m in RE_LINK.finditer(text)]


def purify(text: str) -> str:
    """消息净化器：剔除广告行，保留核心资源信息。"""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if RE_AD.search(line) and not RE_LINK.search(line):
            continue
        lines.append(line)
    return "\n".join(lines)


_AI_PROMPT = (
    "从以下影视资源标题中提取信息，只输出JSON，字段："
    "title(中文名),year,season(数字或null),episodes(数字数组),resolution(如1080P/4K),codec,source。标题：")


async def ai_parse_hub(title: str) -> dict | None:
    """wb-hub（OpenAI 兼容接口）AI 解析。默认走飞牛上的 workbuddy2api-hub 反代。"""
    base = config.get("ai.base_url").rstrip("/")
    key = config.get("ai.api_key")
    if not base or not key:
        return None
    model = config.get("ai.model", "deepseek-v4.1-flash")
    try:
        from .http import client
        async with client(timeout=40) as c:
            r = await c.post(f"{base}/chat/completions",
                             headers={"Authorization": f"Bearer {key}"},
                             json={"model": model,
                                   "messages": [{"role": "user", "content": _AI_PROMPT + title}],
                                   "response_format": {"type": "json_object"},
                                   "temperature": 0})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            return json.loads(text)
    except Exception as e:
        log("WARN", "parser", f"hub AI 解析失败: {e}")
        return None


async def ai_parse_gemini(title: str) -> dict | None:
    """Gemini AI 兜底解析。"""
    key = config.get("gemini.api_key")
    if not key:
        return None
    model = config.get("gemini.model", "gemini-2.0-flash")
    try:
        from .http import client
        async with client("tmdb.proxy", timeout=30) as c:
            r = await c.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": key},
                json={"contents": [{"parts": [{"text": _AI_PROMPT + title}]}],
                      "generationConfig": {"responseMimeType": "application/json"}})
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
    except Exception as e:
        log("WARN", "parser", f"Gemini 解析失败: {e}")
        return None


async def ai_parse(title: str) -> dict | None:
    """AI 解析：wb-hub 优先，Gemini 兜底。"""
    return await ai_parse_hub(title) or await ai_parse_gemini(title)


async def smart_parse(title: str) -> dict:
    """混合解析：先正则，信息不足再走 AI。"""
    r = parse_title(title)
    if r["season"] is None and not r["episodes"] and not r["resolution"]:
        ai = await ai_parse(title)
        if ai:
            r.update({k: v for k, v in ai.items() if v})
    return r
