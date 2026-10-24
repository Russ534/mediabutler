"""MediaButler 入口：FastAPI 应用 + 生命周期（调度器、TG 监听）。"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .database import log
from .scheduler import start as start_scheduler
from .services.tg_monitor import start_listener


@asynccontextmanager
async def lifespan(app: FastAPI):
    pwd = config.ensure_admin_password()
    if pwd:
        print("=" * 60, flush=True)
        print(f"  MediaButler 首次启动，初始登录凭据：", flush=True)
        print(f"  账号: admin   密码: {pwd}", flush=True)
        print(f"  请登录后立即在「设置 → 账户」中修改密码！", flush=True)
        print("=" * 60, flush=True)
    start_scheduler()
    try:
        await start_listener()
    except Exception as e:
        log("WARN", "main", f"TG 监听启动失败（不影响其他功能）: {e}")
    log("INFO", "main", f"MediaButler 启动完成，端口 {config.WEB_PORT}")
    yield
    from .services.tg_monitor import stop_listener
    await stop_listener()


app = FastAPI(title="MediaButler", version="2.0.3", lifespan=lifespan)

from .api.auth import router as auth_router
from .api.routes import router as api_router
from .gateway.proxy import router as gateway_router

app.include_router(auth_router)
app.include_router(api_router)
app.include_router(gateway_router)

WEB_DIR = config.Path(__file__).parent / "web" / "static"


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/version", include_in_schema=False)
async def version():
    return {"name": "MediaButler", "version": "2.0.3"}


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
