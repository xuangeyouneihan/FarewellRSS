import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request


def _env_clean_os_overrides(path: str, os_keys: set[str]) -> None:
    """删除 .env 里所有已经被 OS 环境变量覆盖的条目"""
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    new_lines = []
    removed = False
    for line in lines:
        if "=" in line:
            key = line.split("=", 1)[0]
            if key in os_keys:
                removed = True
                continue
        new_lines.append(line)
    if removed:
        with open(path, "w") as f:
            f.writelines(new_lines)


def _env_upsert_line(path: str, key: str, value: str) -> None:
    """在 .env 文件里原地更新或追加 key=value"""
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    for i, line in enumerate(lines):
        if line.startswith(f"{key}=") or line == f"{key}\n" or line == f"{key}":
            lines[i] = f"{key}={value}\n"
            with open(path, "w") as f:
                f.writelines(lines)
            return
    with open(path, "a") as f:
        f.write(f"{key}={value}\n")


# ---- 通用规则：OS 环境变量 > .env ----
_os_keys = set(os.environ.keys())  # 拍照，趁我们还没往里写东西

# 数据目录由 OS 环境变量决定（Docker ENV / 命令行），不从 .env 读取
DATA_DIR = os.getenv("FAREWELL_RSS_DATA_DIR", "data")
DATA_DIR = os.path.abspath(DATA_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
os.environ["FAREWELL_RSS_DATA_DIR"] = DATA_DIR

env_path = os.path.join(DATA_DIR, ".env")

load_dotenv(env_path, override=False)  # dotenv 不会覆盖已有的 OS 变量
_env_clean_os_overrides(env_path, _os_keys)  # 清理 .env 里被 OS 覆盖的条目

# ---- 密钥：没有就生成 ----
if not os.getenv("FAREWELL_RSS_SECRET"):
    secret = secrets.token_hex(32)
    _env_upsert_line(env_path, "FAREWELL_RSS_SECRET", secret)
    os.environ["FAREWELL_RSS_SECRET"] = secret

from .api.auth import router as auth_router
from .api.label import router as label_router
from .api.misc import router as misc_router
from .api.stream import router as stream_router
from .api.subscriptions import router as subscriptions_router
from .db.db import init_db
from .scheduler.scheduler import run as scheduler_run


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await init_db()
    task = asyncio.create_task(scheduler_run())
    yield
    task.cancel()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)

# Google Reader API — 多个入口兼容不同客户端
app.include_router(auth_router, prefix="/api")
app.include_router(auth_router, prefix="/api/greader")
app.include_router(auth_router, prefix="/api/greader.php")
app.include_router(label_router, prefix="/api")
app.include_router(label_router, prefix="/api/greader")
app.include_router(label_router, prefix="/api/greader.php")
app.include_router(misc_router, prefix="/api")
app.include_router(misc_router, prefix="/api/greader")
app.include_router(misc_router, prefix="/api/greader.php")
app.include_router(stream_router, prefix="/api")
app.include_router(stream_router, prefix="/api/greader")
app.include_router(stream_router, prefix="/api/greader.php")
app.include_router(subscriptions_router, prefix="/api")
app.include_router(subscriptions_router, prefix="/api/greader")
app.include_router(subscriptions_router, prefix="/api/greader.php")

_logger = logging.getLogger(__name__)


def _find_frontend_dist() -> str | None:
    """按优先级找前端构建产物：包内 static → 项目 frontend/dist"""
    # 1. 包内（pip 安装时打进来的）
    pkg_static = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isfile(os.path.join(pkg_static, "index.html")):
        return pkg_static
    # 2. 项目目录 frontend/dist（本地开发/自部署）
    proj_dist = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
    )
    if os.path.isfile(os.path.join(proj_dist, "index.html")):
        return proj_dist
    return None


_FRONTEND_DIST = _find_frontend_dist()


@app.exception_handler(Exception)
async def _catch_all(request: Request, exc: Exception):
    # API 自己抛出的 HTTPException 不记录日志，其他未处理异常才记录
    if not isinstance(exc, HTTPException):
        _logger.exception("未处理异常: %s %s", request.method, request.url)
    raise exc


if _FRONTEND_DIST:
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    # 静态资源（带 hash 的 assets）直接服务
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")),
        name="assets",
    )

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str = ""):
        """SPA fallback：/api 走 API，其余路径返回 index.html（Vue Router history 模式）"""
        # 兜底：/api 开头但没匹配到 API 路由的，返回 404 而不是 index.html
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="Not Found")
        # 静态文件（favicon 等在 dist 根目录）存在则直接返回
        if full_path:
            candidate = os.path.join(_FRONTEND_DIST, full_path)
            if os.path.isfile(candidate):
                return FileResponse(candidate)
        return FileResponse(os.path.join(_FRONTEND_DIST, "index.html"))

    _logger.info("前端静态文件目录: %s", _FRONTEND_DIST)
else:
    _logger.warning("未找到前端构建产物（frontend/dist 或包内 static），仅提供 API")

    @app.get("/")
    async def root():
        return {"message": "FarewellRSS API（前端未构建）"}


def main() -> None:
    import uvicorn
    import winuvloop

    winuvloop.install()

    LOG_CONFIG = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            },
        },
        "loggers": {
            "farewell_rss": {"level": "INFO"},
            "uvicorn": {"level": "INFO"},
        },
        "root": {"level": "WARNING", "handlers": ["default"]},
    }

    host = os.getenv("FAREWELL_RSS_HOST", "0.0.0.0")
    port = int(os.getenv("FAREWELL_RSS_PORT", "3000"))
    uvicorn.run(app, host=host, port=port, loop="asyncio", log_config=LOG_CONFIG)
