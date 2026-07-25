import logging
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...feed_fetcher.feed_fetcher import FetchedEntry
from ..models import Entry
from .enclosure import EnclosureRepository

_logger = logging.getLogger(__name__)


class EntryRepository:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._enclosure_repository = EnclosureRepository(session)

    async def get(self, id_: int) -> Entry | None:
        _logger.debug("获取条目 %d", id_)
        return await self._session.get(Entry, id_)

    async def get_batch(self, ids: list[int]) -> dict[int, Entry]:
        if not ids:
            _logger.debug("批量获取条目，ids 为空")
            return {}
        result = await self._session.execute(select(Entry).where(Entry.id.in_(ids)))
        entries = result.scalars().all()
        _logger.debug("批量获取 %d 条条目，获取到 %d 条", len(ids), len(entries))
        return {entry.id: entry for entry in entries}

    async def get_by_feed_and_guid(self, feed_id: int, guid: str) -> Entry | None:
        _logger.debug("获取条目，feed_id: %d, guid: %s", feed_id, guid)
        return await self._session.scalar(
            select(Entry).where(Entry.feed_id == feed_id, Entry.guid == guid)
        )

    async def list_by_feed(
        self,
        feed_id: int,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Entry]:
        _logger.debug(
            "获取订阅 %d 的条目，起始时间 %s, 结束时间 %s", feed_id, start, end
        )
        query = select(Entry).where(Entry.feed_id == feed_id)
        effective = func.coalesce(
            Entry.published, Entry.updated, Entry.fetched
        )  # published, updated, fetched 三者取其一，优先级为 published > updated > fetched
        if start is not None:
            query = query.where(effective >= start)
        if end is not None:
            query = query.where(effective <= end)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def upsert_by_feed(
        self, feed_id: int, entries: list[FetchedEntry], commit: bool = True
    ) -> list[Entry]:
        results = []

        for entry in entries:
            result = await self.get_by_feed_and_guid(feed_id, entry.guid)
            if result:
                _logger.debug(
                    "更新条目，id：%d, feed_id: %d, guid: %s",
                    result.id,
                    feed_id,
                    entry.guid,
                )
                result.title = entry.title if entry.title else result.title
                result.link = entry.link if entry.link else result.link
                result.published = (
                    entry.published if entry.published else result.published
                )
                result.updated = entry.updated if entry.updated else result.updated
                result.fetched = entry.fetched
                result.summary = entry.summary if entry.summary else result.summary
                result.summary_plain = (
                    entry.summary_plain if entry.summary_plain else result.summary_plain
                )
                result.content = entry.content if entry.content else result.content
                result.content_plain = (
                    entry.content_plain if entry.content_plain else result.content_plain
                )
                result.author_name = (
                    entry.author.name if entry.author else result.author_name
                )
                result.author_href = (
                    entry.author.href if entry.author else result.author_href
                )
                result.author_email = (
                    entry.author.email if entry.author else result.author_email
                )
                result.tags = (
                    str([tag.label or tag.term for tag in entry.tags])
                    if entry.tags
                    else result.tags
                )
            else:
                _logger.debug("插入条目，feed_id: %d, guid: %s", feed_id, entry.guid)
                result = Entry(
                    feed_id=feed_id,
                    guid=entry.guid,
                    title=entry.title,
                    link=entry.link,
                    published=entry.published,
                    updated=entry.updated,
                    fetched=entry.fetched,
                    summary=entry.summary,
                    summary_plain=entry.summary_plain,
                    content=entry.content,
                    content_plain=entry.content_plain,
                    author_name=entry.author.name if entry.author else None,
                    author_href=entry.author.href if entry.author else None,
                    author_email=entry.author.email if entry.author else None,
                    tags=str([tag.label or tag.term for tag in entry.tags])
                    if entry.tags
                    else None,
                )
                self._session.add(result)

            await self._session.flush()

            await self._enclosure_repository.update_by_entry(
                result.id, entry.enclosures, commit=False
            )

            results.append(result)

        if commit:
            await self._session.commit()
        else:
            await self._session.flush()

        return results

    async def delete_batch(self, entries: list[Entry]) -> None:
        for entry in entries:
            await self._enclosure_repository.delete_by_entry(entry.id)
            _logger.debug("删除条目 %d", entry.id)
            await self._session.delete(entry)
        await self._session.commit()

    async def entry_count(self, feed_id: int) -> int:
        entries = await self._session.execute(
            select(func.count()).select_from(Entry).where(Entry.feed_id == feed_id)
        )
        result = entries.scalar_one_or_none()
        if not result:
            result = 0
        _logger.debug("订阅 %d 的条目数量为 %d", feed_id, result)
        return result

    async def search(self, query: str, limit: int = 20, offset: int = 0) -> list[Entry]:
        """FTS5 全文搜索，支持布尔表达式和短语"""
        _logger.debug("搜索条目: %s", query)
        result = await self._session.execute(
            text(
                "SELECT e.* FROM entries e "
                "JOIN entry_fts ON e.id = entry_fts.rowid "
                "WHERE entry_fts MATCH :query "
                "ORDER BY rank "
                "LIMIT :limit OFFSET :offset"
            ),
            {"query": query, "limit": limit, "offset": offset},
        )
        return list(result.scalars().all())
