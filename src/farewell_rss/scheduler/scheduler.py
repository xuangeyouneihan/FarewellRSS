import asyncio
import logging
import os
from datetime import UTC, datetime

from ..db.db import SessionLocal
from ..factory import ServiceBundle, build_services
from ..services.feed import FeedService
from ..services.subscription import SubscriptionService

_logger = logging.getLogger(__name__)

_REFRESH_INTERVAL = int(os.getenv("FAREWELL_RSS_FEED_REFRESH_INTERVAL", "900"))  # 15min
_DEFAULT_TTL = int(os.getenv("FAREWELL_RSS_FEED_DEFAULT_TTL", "3600"))  # 1h
_MIN_TTL = int(os.getenv("FAREWELL_RSS_FEED_MIN_TTL", "900"))  # 15min 最低限制
_MAX_CONCURRENCY = int(
    os.getenv("FAREWELL_RSS_FEED_UPDATE_MAX_CONCURRENCY", "10")
)  # 最大并发数


async def _update_all_feeds(
    feed_service: FeedService, subscription_service: SubscriptionService
) -> None:
    """
    更新所有订阅源的内容。
    """
    _logger.info("开始更新所有订阅源")
    feeds = await feed_service.list_()
    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

    subscription_counts = await subscription_service.subscription_count_batch([
        f.id for f in feeds
    ])

    async def _update_one(feed):
        if feed.id not in subscription_counts:
            _logger.info(
                "订阅源 %s（%d）没有订阅者，跳过更新",
                feed.title or feed.href,
                feed.id,
            )
            return
        ttl = max(_MIN_TTL, feed.ttl) if feed.ttl is not None else _DEFAULT_TTL
        if (
            feed.fetched is not None
            and (datetime.now(UTC) - feed.fetched).total_seconds() < ttl
        ):
            _logger.info(
                "订阅源 %s（%d）在 TTL 内，跳过更新",
                feed.title or feed.href,
                feed.id,
            )
            return
        async with semaphore:
            try:
                await feed_service.update(feed)
            except Exception:
                _logger.exception(
                    "更新订阅源 %s（%d）时发生错误",
                    feed.title or feed.href,
                    feed.id,
                )

    await asyncio.gather(*[_update_one(f) for f in feeds])
    _logger.info("完成更新所有订阅源")


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
        async with SessionLocal() as session:
            services = build_services(session)
            await _update_all_feeds(services.feed, services.subscription)
        # 清理单独开一个 session：抓取和清理是两件独立的事，
        # 不共享事务也就不存在"抓取失败连累清理"的情况
        async with SessionLocal() as session:
            services = build_services(session)
            # 先扫掉指向已不存在源的订阅（正常不该有，见那个方法的注释），再清理
            # 孤儿源 —— 两者都是「用扫描表达状态」的幂等清理
            await services.subscription.prune_orphan_subscriptions()
            await prune_orphan_feeds(services)
        await asyncio.sleep(_REFRESH_INTERVAL)
