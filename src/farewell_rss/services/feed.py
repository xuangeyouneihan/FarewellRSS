import logging
import os
from datetime import UTC, datetime

from ..db.models import Feed
from ..db.repositories.feed import FeedRepository
from ..feed_fetcher.feed_fetcher import FetchError, fetch
from .entry import EntryService

_logger = logging.getLogger(__name__)

# 假 304 兜底：不管服务端怎么回 304，每这么久至少完整抓一次（默认一天）
_FULL_REFRESH_INTERVAL = int(
    os.getenv("FAREWELL_RSS_FEED_FULL_REFRESH_INTERVAL", "86400")
)


class FeedService:
    def __init__(self, repository: FeedRepository, entry_service: EntryService):
        self._repository = repository
        self._entry_service = entry_service

    async def get(self, id_: int) -> Feed | None:
        return await self._repository.get(id_)

    async def get_batch(self, ids: list[int]) -> dict[int, Feed]:
        return await self._repository.get_batch(ids)

    async def get_by_href(self, href: str) -> Feed | None:
        return await self._repository.get_by_href(href)

    async def list_(self) -> list[Feed]:
        return await self._repository.list_()

    async def insert_by_href(self, href: str) -> Feed | None:
        try:
            feed = await fetch(href)
        except FetchError:
            return None
        if feed:
            _logger.info("已从 %s 获取订阅源 %s", href, feed.title)
            return await self._repository.upsert(feed)
        # 此处不记日志，因为 feed_fetcher 那里已经记录了日志
        return None

    async def update(self, feed: Feed) -> Feed | None:
        # 假 304 兜底：弱 ETag（按定义只保证语义等价、不保证字节相同）、秒级
        # Last-Modified（同一秒内改了内容，服务端 mtime_sec <= If-Modified-Since 就回
        # 304，合法实现也会犯）、CDN/反代拿旧对象比对……都会让服务端回一个「内容其实
        # 变了」的 304。而 304 分支只会 touch，于是这个源静默地永远不再更新 —— 周期性
        # 丢掉条件头是唯一能覆盖以上所有原因的兜底，代价是每源每天多一次完整下载。
        full = (
            feed.full_fetched is None
            or (datetime.now(UTC) - feed.full_fetched).total_seconds()
            >= _FULL_REFRESH_INTERVAL
        )
        try:
            if full:
                _logger.info(
                    "订阅源 %s（%s）距上次完整抓取已超过 %d 秒，本次不带条件头全量抓取",
                    feed.title or feed.href,
                    feed.href,
                    _FULL_REFRESH_INTERVAL,
                )
                updated_feed = await fetch(feed.href)
            else:
                updated_feed = await fetch(
                    feed.href, etag=feed.etag, modified=feed.modified
                )
        except FetchError:
            # 网络失败：不更新时间戳，下一轮 TTL 后重试
            return None
        if updated_feed:
            _logger.info("已更新订阅源 %s（%s）", updated_feed.title, updated_feed.href)
            return await self._repository.upsert(updated_feed)
        # 304 未修改或没有条目：更新时间戳，避免反复请求
        await self._repository.touch(feed.id, full=full)
        return None

    async def prune(self, feed: Feed) -> Feed | None:
        if not await self._entry_service.prune_by_feed(feed):
            _logger.info(
                "订阅源 %s（%s）已被清理为空，删除该订阅源", feed.title, feed.href
            )
            await self._repository.delete(feed)
            return None
        _logger.debug(
            "订阅源 %s（%s）已被清理但未为空，保留该订阅源", feed.title, feed.href
        )
        return feed
