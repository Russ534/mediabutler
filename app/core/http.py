"""共享 HTTP 客户端：自动带代理、UA、重试。"""
import httpx

from .. import config

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def client(proxy_key: str = "net.proxy", timeout: float = 30.0, **kw) -> httpx.AsyncClient:
    proxy = config.get(proxy_key) or config.get("net.proxy") or None
    return httpx.AsyncClient(
        timeout=timeout,
        proxy=proxy,
        headers={"User-Agent": UA},
        follow_redirects=True,
        **kw,
    )
