import asyncio
import logging
import os
import time
from datetime import UTC, datetime

from ..db.db import SessionLocal
from ..factory import ServiceBundle, build_services
from ..feed_fetcher.feed_fetcher import FETCH_TIMEOUT

_logger = logging.getLogger(__name__)

_REFRESH_INTERVAL = int(os.getenv("FAREWELL_RSS_FEED_REFRESH_INTERVAL", "900"))  # 15min
_DEFAULT_TTL = int(os.getenv("FAREWELL_RSS_FEED_DEFAULT_TTL", "3600"))  # 1h
_MIN_TTL = int(os.getenv("FAREWELL_RSS_FEED_MIN_TTL", "900"))  # 15min 最低限制
_MAX_CONCURRENCY = int(
    os.getenv("FAREWELL_RSS_FEED_UPDATE_MAX_CONCURRENCY", "10")
)  # 最大并发数

# 单个源从「开抓」到「落库」的时间上限：抓取本身已由 `FAREWELL_RSS_FEED_FETCH_TIMEOUT`
# （默认 30s）兜底，这里再宽一倍多，只兜任何**非网络**的挂死。
#
# 为什么是 per-feed 而不是整轮：整轮的上界取决于源的数量，源多的实例会被误伤；
# per-feed 的上界是确定的（一轮最长 = ceil(源数 / 并发) × 这个值）。重要的是它给
# 「这一轮一定会结束」上了硬保证 —— 线上那次的症状正是 run() 卡在一轮里、
# 之后 8 小时零 tick（Web 还活着，进程没重启）。
_FEED_TIMEOUT = FETCH_TIMEOUT * 2 + 15

# 单个源抓取超过这么久就打 WARNING：排查「谁把这一轮拖住了」全靠它
_SLOW_FEED_WARN = 10.0

# 同一时刻只允许一轮刷新（见 update_all_feeds 的注释）
_update_lock = asyncio.Lock()


def _should_update(feed, subscription_counts: dict[int, int]) -> bool:
    """这个源这一轮要不要抓：没人订阅、或者还在 TTL 内就不抓

    纯只读判断，所以放在「开并发之前」那一段里串行做（订阅数还能一次批量查）。
    """
    if feed.id not in subscription_counts:
        _logger.info(
            "订阅源 %s（%d）没有订阅者，跳过更新", feed.title or feed.href, feed.id
        )
        return False
    ttl = max(_MIN_TTL, feed.ttl) if feed.ttl is not None else _DEFAULT_TTL
    if (
        feed.fetched is not None
        and (datetime.now(UTC) - feed.fetched).total_seconds() < ttl
    ):
        _logger.info(
            "订阅源 %s（%d）在 TTL 内，跳过更新", feed.title or feed.href, feed.id
        )
        return False
    return True


async def update_all_feeds() -> None:
    """更新所有订阅源（同一时刻只跑一轮，重叠的调用直接跳过）

    两种触发源会真的并行：导入 OPML 后的 `jobs.refresh_all_feeds` 和调度器的 tick。
    实测见过两边同时打「距上次完整抓取已超过 86400 秒」——同一批源被同时抓两遍、
    双倍请求，还会互相推翻对方的时间戳判断。

    加锁而不排队：排队的那一轮拿到锁时数据已经旧了，白跑一遍；而新导入的源
    `fetched` 是 `NEVER_FETCHED`，下一轮调度照样会把它挑出来。
    """
    if _update_lock.locked():
        _logger.info("已有一轮刷新在进行中，跳过本轮")
        return
    # 检查与加锁之间没有 await，单线程事件循环里不存在竞态
    async with _update_lock:
        await _update_all_feeds_locked()


async def _update_all_feeds_locked() -> None:
    """更新所有订阅源的内容

    **每个并发任务自己开一个 session。** `AsyncSession` 不能被两个任务并发使用，而这里
    最多有 `_MAX_CONCURRENCY` 个源同时在抓、最后都要写库（`upsert`）。旧写法是整个
    `run()` 只建一个 session 传进来：实测 12 个源（并发上限 10）就会冒出 11 条
    `Session is already flushing`，条目只写进 10/12、源的时间戳只更新 1/12 —— 而且会
    被 per-feed 的 except 吞成一条日志（静默降级：时间戳没更新 → 每轮重复抓）。

    分两阶段：先用一个 session **串行**挑出「要抓哪些源」（全是只读判断），再把
    feed_id 交给并发任务 —— ORM 对象不跨 session 传递。

    测试要换库，照 `jobs_test.py` 的做法 monkeypatch 本模块的 `SessionLocal`。
    """
    _logger.info("开始更新所有订阅源")
    started = time.monotonic()
    async with SessionLocal() as session:
        services = build_services(session)
        feeds = await services.feed.list_()
        subscription_counts = await services.subscription.subscription_count_batch([
            f.id for f in feeds
        ])
        todo = [
            (feed.id, feed.title or feed.href)
            for feed in feeds
            if _should_update(feed, subscription_counts)
        ]

    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _update_one(feed_id: int, title: str) -> None:
        async with semaphore:
            feed_started = time.monotonic()
            try:
                async with SessionLocal() as session:
                    services = build_services(session)
                    feed = await services.feed.get(feed_id)
                    if feed is None:
                        # 挑完之后到抓之前被删掉了（退订 + 清理跑在了中间），跳过
                        _logger.debug("订阅源（%d）已不存在，跳过更新", feed_id)
                        return
                    # 看门狗：这一个源**必须**有上界。超时会把 async with 里的活儿全取消
                    # （含 update 内部的抓取与写库），session 跟着回滚 —— 时间戳没动
                    # → 下一轮重试，不会变成「标成抓过了但没条目」。
                    async with asyncio.timeout(_FEED_TIMEOUT):
                        await services.feed.update(feed)
                        # 这个源自己一个事务：源元数据 + 条目 + 附件要么全落库、要么全不落。
                        # repository 只 flush，所以 commit 必须在这里——少了它，session
                        # 关闭时静默回滚，而源已经被标成“刚抓过”，TTL 内不会再抓。
                        await session.commit()
            except TimeoutError:
                _logger.warning(
                    "更新订阅源 %s（%d）超过 %.0f 秒仍未结束，放弃这个源（下一轮重试）",
                    title,
                    feed_id,
                    _FEED_TIMEOUT,
                )
            except Exception:
                _logger.exception("更新订阅源 %s（%d）时发生错误", title, feed_id)
            elapsed = time.monotonic() - feed_started
            if elapsed >= _SLOW_FEED_WARN:
                _logger.warning(
                    "订阅源 %s（%d）本轮耗时 %.1f 秒", title, feed_id, elapsed
                )

    await asyncio.gather(*[_update_one(feed_id, title) for feed_id, title in todo])
    _logger.info("完成更新所有订阅源，耗时 %.1f 秒", time.monotonic() - started)


async def prune_orphan_feeds(services: ServiceBundle) -> list[int]:
    """清理已无任何订阅者的「孤儿源」，返回被删除的源 id

    清理逻辑复用 FeedService.prune：有 read/star 记录保护的条目会留下，
    此时源本身也保留（它的条目仍被引用，是阅读历史的一部分）。
    因此这个函数是**幂等**的，重复调用只是把同一批孤儿源再检查一遍。

    用周期性扫描而不是在退订/取消收藏处挂钩子，理由：
    那些钩子只能覆盖"事件"，而垃圾的条件是个"状态"——源没有订阅者、
    条目不再被任何记录引用。用扫描表达状态更准确，也能覆盖以后新增的
    所有删除路径，且不需要让用户请求承担破坏性操作。
    """
    deleted: list[int] = []
    # 源列表只取一次：取两次的话，两次查询之间新建的源/订阅会让判断不一致
    # （第二个列表里的源可能在计数里查不到，被当成孤儿）。计数也一次批量查完，
    # 不再是「每个源一次 COUNT」。
    feeds = await services.feed.list_()
    subscription_counts = await services.subscription.subscription_count_batch([
        f.id for f in feeds
    ])
    for feed in feeds:
        feed_id = feed.id
        if feed_id in subscription_counts:
            continue
        _logger.info(
            "订阅源 %s（%d）没有订阅者，尝试清理", feed.title or feed.href, feed_id
        )
        try:
            if await services.feed.prune(feed) is None:
                deleted.append(feed_id)
        except Exception:
            # 单个源出错不能把整轮清理带走：run() 的 while True 没有兜底，异常跑出去
            # 这个任务就再也不会醒来了（对齐 _update_one 的做法）
            _logger.exception(
                "清理订阅源 %s（%d）时发生错误", feed.title or feed.href, feed_id
            )
    if deleted:
        _logger.info("已清理 %d 个孤儿源：%s", len(deleted), deleted)
    return deleted


async def run() -> None:
    """运行定时任务：定期更新所有订阅源的内容，并清理孤儿源"""
    _logger.info(
        "定时任务已启动，刷新间隔 %ds，默认 TTL %ds，最低 TTL %ds",
        _REFRESH_INTERVAL,
        _DEFAULT_TTL,
        _MIN_TTL,
    )
    while True:
        try:
            await update_all_feeds()
        except Exception:
            # 单轮出错不能让 while True 退出：main.py 用 create_task 起这个任务，
            # 异常跑出去就再也没人重启，之后整个实例既不刷新也不清理
            _logger.exception("刷新订阅源时出错，跳过这一轮")
        # 清理单独开一个 session：抓取和清理是两件独立的事，
        # 不共享事务也就不存在"抓取失败连累清理"的情况
        try:
            async with SessionLocal() as session:
                services = build_services(session)
                # 先扫掉指向已不存在源的订阅（正常不该有，见那个方法的注释），再清理
                # 孤儿源 —— 两者都是「用扫描表达状态」的幂等清理
                await services.subscription.prune_orphan_subscriptions()
                await prune_orphan_feeds(services)
                # 清理块自己的事务边界（repository 只 flush）
                await session.commit()
        except Exception:
            _logger.exception("清理孤儿源时出错，跳过这一轮")
        await asyncio.sleep(_REFRESH_INTERVAL)
