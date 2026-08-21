import logging
from datetime import datetime

from ..db.models import Entry, Feed
from ..db.repositories.feed import FeedRepository
from ..feed_fetcher.feed_fetcher import FetchError, fetch
from .entry import EntryService

_logger = logging.getLogger(__name__)


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
        try:
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
        await self._repository.touch(feed.id)
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

    async def list_entries(
        self, feed: Feed, start: datetime | None = None, end: datetime | None = None
    ) -> list[Entry]:
        entries = await self._entry_service.list_by_feed(feed, start, end)
        return entries
