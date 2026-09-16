"""密码哈希的线程池封装。

bcrypt 是 CPU 密集型调用（默认 cost 约 0.2s），同步阻塞——直接在协程里调用会
卡住事件循环，让所有并发请求一起干等。好消息是它会释放 GIL，所以丢进线程池就能
拿到真并行（实测 4 并发约 4 倍加速）；但它终究在吃 CPU，worker 数超过核数无益。

这里用**专属**线程池把它移出事件循环，而不是 asyncio.to_thread：
to_thread 走的是事件循环的默认执行器，feed_fetcher / scheduler 也在用同一个，
混在一起会互相排队（scheduler 一波 10 个抓取任务就能把默认执行器占满，
登录请求只能排在后面）。专属池还顺带限制了密码运算的并发度——
bcrypt 本身就很贵，不限并发等于给暴力破解留后门。

worker 数由 FAREWELL_RSS_PASSWORD_MAX_CONCURRENCY 配置（见 docs/ENVIRONMENT.md）。
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import bcrypt


def _resolve_max_workers(raw: str | None) -> int:
    """确定密码线程池大小。

    未配置 → min(4, CPU 核数)：bcrypt 会释放 GIL，池内确实在并行吃核，所以线程
    超过核数只会互相抢 CPU。单次约 0.2s，4 个 worker 约合 20 次/秒，对单实例部署
    足够，超出部分在池里排队（天然限流）。

    配置了 → 取它的整数值。非法值就地抛 ValueError，让配置错误在**启动时**暴露，
    而不是等到第一次登录才发现行为和预期不符。
    """
    if raw is None:
        return min(4, os.cpu_count() or 1)
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(
            f"FAREWELL_RSS_PASSWORD_MAX_CONCURRENCY 不是合法整数：{raw!r}"
        ) from e
    if value < 1:
        raise ValueError(
            f"FAREWELL_RSS_PASSWORD_MAX_CONCURRENCY 必须不小于 1，收到：{raw!r}"
        )
    return value


# 调小 = 更严格地限制密码运算并发（防爆破），调大 = 高并发登录时少排队。
_MAX_WORKERS = _resolve_max_workers(os.getenv("FAREWELL_RSS_PASSWORD_MAX_CONCURRENCY"))

_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="bcrypt")


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


async def hash_password(password: str) -> str:
    """在线程池里做 bcrypt 哈希，不阻塞事件循环"""
    return await asyncio.get_running_loop().run_in_executor(_executor, _hash, password)


async def verify_password(password: str, password_hash: str) -> bool:
    """在线程池里校验密码；password_hash 格式非法时抛 ValueError"""
    return await asyncio.get_running_loop().run_in_executor(
        _executor, _verify, password, password_hash
    )
