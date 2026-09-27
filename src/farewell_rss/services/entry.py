import logging
from datetime import datetime

from ..db.models import Entry, Feed
from ..db.repositories.entry import EntryRepository
from ..enums import Filtering, SortOrder

_logger = logging.getLogger(__name__)


class EntryService:
    def __init__(self, repository: EntryRepository):
        self._repository = repository

    async def get(self, id_: int) -> Entry | None:
        return await self._repository.get(id_)

    async def get_batch(self, ids: list[int]) -> dict[int, Entry]:
        return await self._repository.get_batch(ids)

    async def get_by_feed_and_guid(self, feed: Feed, guid: str) -> Entry | None:
        return await self._repository.get_by_feed_and_guid(feed.id, guid)

    async def list_by_feed(
        self,
        feed: Feed,
        user_id: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        include: Filtering | None = None,
        exclude: Filtering | None = None,
        sorting: SortOrder = SortOrder.DESCENDING,
        cursor: tuple[int, int] | None = None,
        limit: int | None = None,
    ) -> list[Entry]:
        return await self._repository.list_by_feed(
            feed_id=feed.id,
            user_id=user_id,
            start=start,
            end=end,
            include=include,
            exclude=exclude,
            sorting=sorting,
            cursor=cursor,
            limit=limit,
        )

    async def list_reading_list(
        self,
        user_id: int,
        start: datetime | None = None,
        end: datetime | None = None,
        include: Filtering | None = None,
        exclude: Filtering | None = None,
        sorting: SortOrder = SortOrder.DESCENDING,
        cursor: tuple[int, int] | None = None,
        limit: int | None = None,
        folder_id: int | None = None,
    ) -> list[Entry]:
        return await self._repository.list_reading_list(
            user_id=user_id,
            start=start,
            end=end,
            include=include,
            exclude=exclude,
            sorting=sorting,
            cursor=cursor,
            limit=limit,
            folder_id=folder_id,
        )

    async def list_starred(
        self,
        user_id: int,
        start: datetime | None = None,
        end: datetime | None = None,
        include: Filtering | None = None,
        exclude: Filtering | None = None,
        sorting: SortOrder = SortOrder.DESCENDING,
        cursor: tuple[int, int] | None = None,
        limit: int | None = None,
        tag_id: int | None = None,
        uncategorized: bool = False,
    ) -> list[Entry]:
        return await self._repository.list_starred(
            user_id,
            start,
            end,
            include,
            exclude,
            sorting,
            cursor,
            limit,
            tag_id,
            uncategorized,
        )

    async def list_read(
        self,
        user_id: int,
        start: datetime | None = None,
        end: datetime | None = None,
        include: Filtering | None = None,
        exclude: Filtering | None = None,
        sorting: SortOrder = SortOrder.DESCENDING,
        cursor: tuple[int, int] | None = None,
        limit: int | None = None,
        history: bool = False,
    ) -> list[Entry]:
        return await self._repository.list_read(
            user_id, start, end, include, exclude, sorting, cursor, limit, history
        )

    async def prune_by_feed(self, feed: Feed) -> int:
        """清掉某个源里「没人真读过也没人收藏」的条目，返回该源还剩多少条目（0 = 已空）

        SQL、跨用户保留条件和事务都在 repository 里（下移之前这里是逐条 ORM 判断：
        取回整个源的条目、批量数计数、逐条清状态再批量删，50 条条目要一百多条 SQL）。
        调用方 `FeedService.prune` 看返回值真假（0 即空）决定要不要删掉整个源。
        """
        return await self._repository.prune_by_feed(feed.id)

    async def entry_count(self, feed: Feed) -> int:
        return await self._repository.entry_count(feed.id)

    async def search(self, query: str, limit: int = 20, offset: int = 0) -> list[Entry]:
        """FTS5 全文搜索"""
        return await self._repository.search(query, limit, offset)
