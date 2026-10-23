"""网关：
1. /stream/{pickcode}/{filename} — STRM 播放入口，302 到 115 CDN 直链
2. /emby/* — Emby 反代（复刻 CMS 9096），图片磁盘缓存，播放路径直连
"""
import hashlib
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from .. import config
from ..database import log

router = APIRouter()

# pickcode → (直链, 过期时间)。115 直链有时效，短缓存
_url_cache: dict[str, tuple[str, float]] = {}
URL_CACHE_TTL = 600


@router.get("/stream/{pickcode}/{filename}")
async def stream(pickcode: str, filename: str):
    import time
    cached = _url_cache.get(pickcode)
    if cached and cached[1] > time.time():
        return RedirectResponse(cached[0], status_code=302)
    try:
        from ..core.pan115 import download_url
        url = await download_url(pickcode)
        _url_cache[pickcode] = (url, time.time() + URL_CACHE_TTL)
        if len(_url_cache) > 5000:
            _url_cache.clear()
        return RedirectResponse(url, status_code=302)
    except Exception as e:
        log("ERROR", "gateway", f"直链获取失败 pickcode={pickcode}: {e}")
        return Response(f"直链获取失败: {e}", status_code=502)


@router.get("/d/{pickcode}")
async def stream_cms_compat(pickcode: str):
    """CMS 9527 兼容路由：旧 STRM 内容为 http://host:9527/d/<pickcode>?/<name>，
    接管 CMS 退役后 28 万存量 STRM 不死链（compose 映射 9527:8866 即可）。"""
    import time
    cached = _url_cache.get(pickcode)
    if cached and cached[1] > time.time():
        return RedirectResponse(cached[0], status_code=302)
    try:
        from ..core.pan115 import download_url
        url = await download_url(pickcode)
        _url_cache[pickcode] = (url, time.time() + URL_CACHE_TTL)
        if len(_url_cache) > 5000:
            _url_cache.clear()
        return RedirectResponse(url, status_code=302)
    except Exception as e:
        log("ERROR", "gateway", f"兼容路由直链失败 pickcode={pickcode}: {e}")
        return Response(f"直链获取失败: {e}", status_code=502)


@router.api_route("/emby/{path:path}", methods=["GET", "HEAD"])
async def emby_proxy(path: str, request: Request):
    """Emby 反代：图片走磁盘缓存，其余透传。"""
    base = config.get("emby.host").rstrip("/")
    if not base:
        return Response("Emby 未配置", status_code=503)
    url = f"{base}/{path}"
    qs = str(request.url.query)
    if qs:
        url += "?" + qs
    # api_key 透传或补上
    if "api_key" not in qs and config.get("emby.api_key"):
        url += ("&" if qs else "?") + "api_key=" + config.get("emby.api_key")

    is_image = "/Images/" in path or path.startswith("Items/") and "/Images" in url
    cache_file = None
    if is_image:
        key = hashlib.md5(url.split("api_key=")[0].encode()).hexdigest()
        cache_file = config.CACHE_DIR / "emby_images" / key
        if cache_file.exists():
            suffix = "jpg"
            return Response(cache_file.read_bytes(), media_type=f"image/{suffix}",
                            headers={"X-Cache": "HIT"})
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": request.headers.get("user-agent", "")})
        content = r.content
        if is_image and r.status_code == 200 and cache_file is not None and len(content) < 20 * 1024 * 1024:
            try:
                cache_file.write_bytes(content)
            except OSError:
                pass
        ctype = r.headers.get("content-type", "application/octet-stream")
        return Response(content, status_code=r.status_code, media_type=ctype,
                        headers={"X-Cache": "MISS" if is_image else "BYPASS"})
    except Exception as e:
        return Response(f"Emby 反代失败: {e}", status_code=502)
