"""洗版策略引擎（采用 CMS tidy.yaml 语义）。
规则：按 priority_level 从上到下，先命中的优先级更高；字段间为与，值内逗号为或，! 为排除。
打分返回 (命中层级, 资源质量分)，层级越小越优先；未命中返回 (999, 0)。"""
import re

from .. import config
from ..database import log

DEFAULT_TIDY = """电影洗版策略:
  mode: replace
  media_type: movie
  priority_level:
    - resource_pix: "2160p,4k"
      resource_type: "Remux"
      resource_effect: "!DV"
    - resource_pix: "1080p"
      resource_type: "Remux"
      resource_effect: "!DV"
    - resource_pix: "2160p,4k"
      resource_effect: "!DV"
    - resource_pix: "1080p"
      resource_type: "BluRay"
      resource_effect: "!DV"
    - resource_pix: "1080p"
      resource_effect: "!DV"
剧集洗版策略:
  mode: replace
  media_type: tv
  priority_level:
    - resource_pix: "2160p,4k"
      resource_type: "Remux,BluRay"
      resource_effect: "!DV"
    - resource_pix: "2160p,4k"
      resource_effect: "HDR,HDR10,DV.HDR"
    - resource_pix: "2160p,4k"
    - resource_pix: "1080p"
"""

_RES_RANK = {"8K": 8, "4K": 4, "2160P": 4, "1080P": 3, "720P": 2, "480P": 1}
_TYPE_RANK = {"REMUX": 5, "BLURAY": 4, "BDRIP": 3, "WEB-DL": 3, "WEBRIP": 2, "HDTV": 1}

_cache: dict = {"text": None, "rules": None}


def _parse(text: str) -> dict:
    rules: dict = {}
    cur = None
    in_pri = False
    cur_item = None
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and s.endswith(":"):
            cur = {}
            rules[s[:-1]] = cur
            in_pri = False
        elif cur is not None and indent == 2 and s.startswith("media_type:"):
            cur["media_type"] = s.split(":", 1)[1].strip()
        elif cur is not None and indent == 2 and s.startswith("mode:"):
            cur["mode"] = s.split(":", 1)[1].strip()
        elif cur is not None and indent == 2 and s.startswith("priority_level:"):
            cur["priority"] = []
            in_pri = True
        elif in_pri and s.startswith("- "):
            cur_item = {}
            cur["priority"].append(cur_item)
            kv = s[2:]
            if ":" in kv:
                k, _, v = kv.partition(":")
                cur_item[k.strip()] = v.strip().strip('"').strip("'")
        elif in_pri and cur_item is not None and ":" in s:
            k, _, v = s.partition(":")
            cur_item[k.strip()] = v.strip().strip('"').strip("'")
    return rules


def _rules() -> dict:
    text = config.get("wash.tidy_yaml") or DEFAULT_TIDY
    if _cache["text"] != text:
        _cache["text"] = text
        _cache["rules"] = _parse(text)
    return _cache["rules"]


def _norm_pix(res: str) -> str:
    r = (res or "").upper()
    return {"4K": "2160p", "2160P": "2160p"}.get(r, r.lower())


def _match_field(actual: str, spec: str) -> bool:
    neg = spec.startswith("!")
    if neg:
        spec = spec[1:]
    vals = {v.strip().lower() for v in re.split(r"[,，]", spec) if v.strip()}
    a = (actual or "").lower()
    hit = any(v in a or a == v for v in vals)
    return (not hit) if neg else hit


def score(media_type: str, info: dict) -> tuple[int, int]:
    """info: {resolution, source, team, effect(hdr/dv), fps}。返回 (层级, 质量分)。"""
    rules = [r for r in _rules().values() if r.get("media_type") == media_type]
    pri = rules[0]["priority"] if rules else []
    res = _norm_pix(info.get("resolution", ""))
    src = (info.get("source") or "").upper()
    team = info.get("team") or ""
    effect = (info.get("effect") or "").upper()
    fps = str(info.get("fps") or "")
    fields = {"resource_pix": res, "resource_type": src, "resource_team": team,
              "resource_effect": effect, "resource_version": info.get("version", ""),
              "fps": fps}
    level = 999
    for i, cond in enumerate(pri):
        if all(_match_field(fields.get(k, ""), v) for k, v in cond.items()):
            level = i
            break
    quality = _RES_RANK.get(res.upper(), 0) * 100 + _TYPE_RANK.get(src, 0) * 10
    if "HDR" in effect:
        quality += 5
    return level, quality


def better(media_type: str, new: dict, old: dict) -> bool:
    """新资源是否比旧资源更值得收（洗版判断）。"""
    ln, qn = score(media_type, new)
    lo, qo = score(media_type, old)
    if ln != lo:
        return ln < lo
    return qn > qo
