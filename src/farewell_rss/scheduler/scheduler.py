import asyncio
import logging
import os
from datetime import UTC, datetime

from ..db.db import SessionLocal
from ..db.repositories.entry import EntryRepository
from ..db.repositories.feed import FeedRepository
from ..db.repositories.read_state import ReadStateRepository
from ..db.repositories.star_state import StarStateRepository
from ..db.repositories.subscription import SubscriptionRepository
from ..services.entry import EntryService
from ..services.feed import FeedService
from ..services.read_state import ReadStateService
from ..services.star_state import StarStateService
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

    async def _update_one(feed):
        if await subscription_service.subscription_count(feed) <= 0:
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


async def run() -> None:
    """运行定时任务，定期更新所有订阅源的内容"""
    _logger.info(
        "定时任务已启动，刷新间隔 %ds，默认 TTL %ds，最低 TTL %ds",
        _REFRESH_INTERVAL,
        _DEFAULT_TTL,
        _MIN_TTL,
    )
    while True:
        async with SessionLocal() as session:
            read_state_service = ReadStateService(
                repository=ReadStateRepository(session=session)
            )
            star_state_service = StarStateService(
                repository=StarStateRepository(session=session)
            )
            entry_service = EntryService(
                repository=EntryRepository(session=session),
                read_state_service=read_state_service,
                star_state_service=star_state_service,
            )
            feed_service = FeedService(
                repository=FeedRepository(session=session),
                entry_service=entry_service,
            )
            subscription_service = SubscriptionService(
                repository=SubscriptionRepository(session=session),
                feed_service=feed_service,
                read_state_service=read_state_service,
                star_state_service=star_state_service,
            )
            await _update_all_feeds(feed_service, subscription_service)
        await asyncio.sleep(_REFRESH_INTERVAL)
