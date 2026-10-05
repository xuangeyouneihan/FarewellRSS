"""后台作业：脱离请求生命周期运行，因此**自建 session**。

与 scheduler 的分工：scheduler 是常驻的定时轮询，这里是「由某个请求触发、
但要在请求结束之后继续跑」的一次性清理。

两者共同的前提：**不能复用请求作用域的 service**。请求结束时 `get_session`
会提交并关闭那个 session，而 AsyncSession 不允许被两个任务并发使用——
后台任务和请求收尾会互相踩踏（这是拿请求 session 去 create_task 的隐患）。

**提交也由这里负责**：repository 只 flush、不 commit，所以自建 session 的地方必须
自己 commit，否则 `async with` 退出时静默回滚（不报错，日志还会说“完成”）。
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from .db.db import SessionLocal
from .factory import build_services
from .scheduler.scheduler import update_all_feeds

_logger = logging.getLogger(__name__)

# 后台作业的引用必须存住：asyncio.create_task() 的返回值没人引用时，任务可能在跑完
# 之前就被 GC 掉（官方文档明确要求保存引用）。这些作业被回收掉就是静默不执行。
_background_tasks: set[asyncio.Task] = set()


def _log_failure(task: asyncio.Task) -> None:
    """作业失败要留痕

    `spawn` 的调用方不会 await 这个任务，异常否则只在任务被 GC 时以
    「Task exception was never retrieved」的形式出现 —— 级别不可控、还可能整条丢掉。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _logger.error("后台作业失败：%s", exc, exc_info=exc)


def spawn(coro: Coroutine[Any, Any, None]) -> None:
    """把作业挂到后台跑（调用方不要 await）"""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    task.add_done_callback(_log_failure)


async def refresh_all_feeds() -> None:
    """立刻抓一轮所有该抓的源（导入 OPML 之后触发）

    语义与调度器每轮**完全相同**（只抓有订阅者且过了 TTL 的源）：新导入的源 `fetched`
    是 `NEVER_FETCHED`，所以必然被挑中。

    为什么由作业触发而不是在请求里 await：一次导入几十个源，同步等会把响应拖到分钟级。
    抓取本身**自带**事务边界（`update_all_feeds` 每个源一个 session），所以脱离请求
    生命周期也没问题。
    """
    await update_all_feeds()


async def hard_delete_user(user_id: int) -> None:
    """彻底删除用户及其全部数据（在 UserService.delete 软删除之后调用）

    用 id 而不是 User 对象作为参数：调用发生在请求里，而执行发生在请求之后，
    传对象就得依赖那个已经关闭的 session。
    """
    _logger.info("开始彻底清理用户 %d 的数据", user_id)
    async with SessionLocal() as session:
        services = build_services(session)
        user = await services.user.get(user_id)
        if user is None:
            _logger.warning("要清理的用户 %d 不存在，跳过", user_id)
            return
        await services.user.purge(user)
        # 这条后台作业自己的事务边界，不能省：session 关闭时静默回滚，
        # 不报错、日志还会打“清理完成”
        await session.commit()
    _logger.info("用户 %d 的数据清理完成", user_id)
