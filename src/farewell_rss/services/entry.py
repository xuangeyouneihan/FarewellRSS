import logging
from datetime import datetime

from ..db.models import Entry, Feed
from ..db.repositories.entry import EntryRepository
from ..enums import Filtering, SortOrder
from .read_state import ReadStateService
from .star_state import StarStateService

_logger = logging.getLogger(__name__)


class EntryService:
    def __init__(
        self,
        repository: EntryRepository,
        read_state_service: ReadStateService,
        star_state_service: StarStateService,
    ):
        self._repository = repository
        self._read_state_service = read_state_service
        self._star_state_service = star_state_service

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

    async def prune_by_feed(self, feed: Feed) -> list[Entry]:
        result = []
        to_be_deleted = []
        entries = await self.list_by_feed(feed)
        read_counts = await self._read_state_service.read_count_batch(entries)
        star_counts = await self._star_state_service.star_count_batch(entries)
        for entry in entries:
            await self._read_state_service.prune_by_entry(entry)
            if star_counts.get(entry.id, 0) == 0 and read_counts.get(entry.id, 0) == 0:
                to_be_deleted.append(entry)
            else:
                result.append(entry)
        _logger.debug(
            "清理订阅 %d 的条目，删除 %d 条，保留 %d 条",
            feed.id,
            len(to_be_deleted),
            len(result),
        )
        await self._repository.delete_batch(to_be_deleted)
        return result

    async def entry_count(self, feed: Feed) -> int:
        return await self._repository.entry_count(feed.id)

    async def search(self, query: str, limit: int = 20, offset: int = 0) -> list[Entry]:
        """FTS5 全文搜索"""
        return await self._repository.search(query, limit, offset)
