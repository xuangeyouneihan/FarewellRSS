"""密码哈希的线程池封装测试。

关键性质：bcrypt 是同步阻塞的（单次约 0.2s），必须离开事件循环——否则一个登录
请求就能把整个服务卡住。它自身会释放 GIL（池内已是真并行），所以这里用
「心跳协程」证明 hash 期间事件循环仍在运转，而不是用来验证并行度。
"""

import asyncio
import os

import pytest

from farewell_rss.services._password import (
    _resolve_max_workers,
    hash_password,
    verify_password,
)

_PASSWORD = "correct horse battery staple"


@pytest.mark.parametrize("raw", ["1", "3", "8", " 8 "])
def test_env_value_is_used(raw):
    assert _resolve_max_workers(raw) == int(raw)


def test_default_is_bounded_by_cpu_count():
    """未配置时取 min(4, CPU 核数)：线程超过核数只会互相抢 CPU，没有收益"""
    workers = _resolve_max_workers(None)
    assert 1 <= workers <= 4
    assert workers <= (os.cpu_count() or 1)


@pytest.mark.parametrize("raw", ["0", "-1", "1.5", "abc", ""])
def test_invalid_value_fails_fast(raw):
    """非法配置必须在启动时就炸，不能静默兜底成某个值"""
    with pytest.raises(ValueError):
        _resolve_max_workers(raw)


async def test_hash_and_verify_roundtrip():
    hashed = await hash_password(_PASSWORD)
    assert hashed != _PASSWORD
    assert hashed.startswith("$2")  # bcrypt 哈希格式
    assert await verify_password(_PASSWORD, hashed)
    assert not await verify_password("wrong password", hashed)


async def test_salts_are_unique():
    first = await hash_password("same password")
    second = await hash_password("same password")
    assert first != second  # 每次 gensalt 都不同
    assert await verify_password("same password", first)
    assert await verify_password("same password", second)


async def test_hash_does_not_block_event_loop():
    """hash 期间事件循环必须还能调度其他协程"""
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        await hash_password(_PASSWORD)
    finally:
        task.cancel()

    # 若 hash 在事件循环里直接跑，ticks 会一直是 0
    assert ticks > 0


async def test_verify_does_not_block_event_loop():
    hashed = await hash_password(_PASSWORD)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        await verify_password(_PASSWORD, hashed)
    finally:
        task.cancel()

    assert ticks > 0
