"""网盘分类规则（采用 CMS category.yaml 语义 + Symedia 二级分类策略）。
按 TMDB 详情（genre_ids / original_language / origin_country）从上到下匹配，
命中即返回相对目录，如「电影/华语电影」「电视剧/日番」。"""
import re

from .. import config
from ..database import log

# 与 CMS 线上 category.yaml 完全一致的默认规则
DEFAULT_YAML = """movie:
  动画电影:
    genre_ids: "16"
  华语电影:
    original_language: "zh,cn,bo,za"
  外语电影:
tv:
  儿童:
    genre_ids: "10762"
  国漫:
    genre_ids: "16"
    origin_country: "CN,TW,HK"
  日番:
    genre_ids: "16"
    origin_country: "JP"
  纪录片:
    genre_ids: "99"
  综艺:
    genre_ids: "10764,10767"
  国产剧:
    origin_country: "CN,TW,HK"
  欧美剧:
    origin_country: "US,FR,GB,DE,ES,IT,NL,PT,RU,UK"
  日韩剧:
    origin_country: "JP,KP,KR,TH,IN,SG"
  未分类:
"""

_cache: dict = {"text": None, "rules": None}


def _parse(text: str) -> dict:
    """极简 YAML 子集解析：两级映射 + key: "a,b,c"。够用且零依赖。"""
    rules: dict = {"movie": [], "tv": []}
    cur_top = None
    cur_name = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        content = line.strip()
        if indent == 0 and content.endswith(":"):
            cur_top = content[:-1].strip()
            cur_name = None
        elif indent == 2 and content.endswith(":"):
            cur_name = content[:-1].strip()
            if cur_top in rules:
                rules[cur_top].append((cur_name, {}))
        elif indent >= 4 and ":" in content and cur_name and cur_top in rules:
            k, _, v = content.partition(":")
            v = v.strip().strip('"').strip("'")
            if v:
                rules[cur_top][-1][1][k.strip()] = v
    return rules


def _rules() -> dict:
    text = config.get("organize.category_yaml") or DEFAULT_YAML
    if _cache["text"] != text:
        _cache["text"] = text
        _cache["rules"] = _parse(text)
    return _cache["rules"]


def classify(media_type: str, detail: dict) -> str:
    """detail 为 TMDB 详情 dict。返回 '电影/华语电影' 这样的相对目录。"""
    top = "movie" if media_type == "movie" else "tv"
    genre_ids = {str(g.get("id")) for g in detail.get("genres") or []}
    lang = (detail.get("original_language") or "").lower()
    if top == "movie":
        countries = {c.get("iso_3166_1", "") for c in detail.get("production_countries") or []}
    else:
        countries = set(detail.get("origin_country") or [])
    last_name = None
    for name, conds in _rules().get(top, []):
        last_name = name
        ok = True
        for field, values in conds.items():
            vals = {v.strip() for v in re.split(r"[,，]", values) if v.strip()}
            if field == "genre_ids":
                ok = bool(genre_ids & vals)
            elif field == "original_language":
                ok = lang in {v.lower() for v in vals}
            elif field in ("origin_country", "production_countries"):
                ok = bool(countries & vals)
            else:
                ok = str(detail.get(field, "")) in vals
            if not ok:
                break
        if ok:
            return f"{'电影' if top == 'movie' else '电视剧'}/{name}"
    # 未匹配：用最后一个分类（CMS 语义：最后一个为兜底）
    if last_name:
        return f"{'电影' if top == 'movie' else '电视剧'}/{last_name}"
    log("WARN", "category", "分类规则为空，落根目录")
    return "电影" if top == "movie" else "电视剧"
