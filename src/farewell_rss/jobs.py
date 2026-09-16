"""后台作业：脱离请求生命周期运行，因此**自建 session**。

与 scheduler 的分工：scheduler 是常驻的定时轮询，这里是「由某个请求触发、
但要在请求结束之后继续跑」的一次性清理。

两者共同的前提：**不能复用请求作用域的 service**。请求结束时 `get_session`
会提交并关闭那个 session，而 AsyncSession 不允许被两个任务并发使用——
后台任务和请求收尾会互相踩踏（这是拿请求 session 去 create_task 的隐患）。
"""

import logging

from .db.db import SessionLocal
from .factory import build_services

_logger = logging.getLogger(__name__)


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
    _logger.info("用户 %d 的数据清理完成", user_id)
