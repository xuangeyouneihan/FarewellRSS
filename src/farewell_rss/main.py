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


@app.exception_handler(Exception)
async def _catch_all(request: Request, exc: Exception):
    # API 自己抛出的 HTTPException 不记录日志，其他未处理异常才记录
    if not isinstance(exc, HTTPException):
        _logger.exception("未处理异常: %s %s", request.method, request.url)
    raise exc


@app.get("/")
async def root():
    return {"message": "Hello World"}


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
