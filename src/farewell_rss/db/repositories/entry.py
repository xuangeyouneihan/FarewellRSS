import logging
from datetime import datetime

from sqlalchemy import Select, and_, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...enums import Filtering, SortOrder
from ...feed_fetcher.feed_fetcher import FetchedEntry
from ..models import Entry, ReadState, StarState, Subscription
from .enclosure import EnclosureRepository

_logger = logging.getLogger(__name__)


def _effective_seconds():
    """「有效时间」的秒级时间戳：published > updated > fetched 取其一后截断到秒。

    排序、范围过滤、分页游标全都用它，好让 SQL 的排序键和 API 契约
    （api/stream.py 的 _entry_sort_key，以及 continuation 里那 16 位 hex 秒）严格一致。
    微秒只有 crawlTimeMsec / timestampUsec 这类展示字段用得上，所以 fetched
    照常带着微秒存，只是不参与这里的比较。
    """
    return func.unixepoch(func.coalesce(Entry.published, Entry.updated, Entry.fetched))


def _to_seconds(value: datetime) -> int:
    """把 datetime 归一到秒级 Unix 时间戳（库里存的都是 UTC）"""
    return int(value.timestamp())


def _has_read(user_id: int, history: bool = False):
    """该条目对用户是否「已读」。

    history=True 只认「真正读过」的（ReadState.timestamp 非空）；批量标已读写的是
    timestamp=None 的 ReadState，那类算已读但不算读过。
    """
    conditions = [Entry.id == ReadState.entry_id, ReadState.user_id == user_id]
    if history:
        conditions.append(ReadState.timestamp.is_not(None))
    return exists(select(1).where(and_(*conditions)))


def _has_starred(user_id: int, tag_id: int | None = None, uncategorized: bool = False):
    """该条目对用户是否已收藏；可按标签（tag_id）或只看未分类（uncategorized）"""
    conditions = [Entry.id == StarState.entry_id, StarState.user_id == user_id]
    if tag_id is not None:
        conditions.append(StarState.tag_id == tag_id)
    elif uncategorized:
        conditions.append(StarState.tag_id.is_(None))
    return exists(select(1).where(and_(*conditions)))


def _state_filters(
    user_id: int, include: Filtering | None, exclude: Filtering | None
) -> list:
    """把 include/exclude 翻成「已读 / 已收藏」的条件（reading-list 和单源流共用）。"""
    _read = _has_read(user_id)
    _starred = _has_starred(user_id)
    clauses = []
    if include == Filtering.READ or exclude == Filtering.UNREAD:
        clauses.append(_read)
    if include == Filtering.UNREAD or exclude == Filtering.READ:
        clauses.append(~_read)
    if include == Filtering.STARRED:
        clauses.append(_starred)
    if exclude == Filtering.STARRED:
        clauses.append(~_starred)
    return clauses


def _stream_query(
    query: Select,
    where_clause: list,
    *,
    start: datetime | None,
    end: datetime | None,
    cursor: tuple[int, int] | None,
    sorting: SortOrder,
    limit: int | None,
) -> Select:
    """挂上三个流（reading-list / starred / read）共用的收尾：时间范围、游标、排序、limit。

    排序键和游标键必须是同一个秒级表达式（_effective_seconds），否则同一秒内的相对顺序
    会和 API 契约对不上，游标就会指错位置（漏行或死循环重复）。

    游标用严格不等号，而且展开成 (时间, id) 两个条件：continuation 里只装得下秒，所以
    「同一秒里 id 更小/更大」的那些条目只能靠第二个条件捞出来；用严格不等号则是因为
    游标那一行在上一页已经返回给客户端了。
    """
    effective = _effective_seconds()
    clauses = list(where_clause)

    if start is not None:
        clauses.append(effective >= _to_seconds(start))
    if end is not None:
        clauses.append(effective <= _to_seconds(end))

    if cursor is not None:
        ts, cursor_id = cursor
        if sorting == SortOrder.ASCENDING:
            clauses.append(
                or_(effective > ts, and_(effective == ts, Entry.id > cursor_id))
            )
        else:
            clauses.append(
                or_(effective < ts, and_(effective == ts, Entry.id < cursor_id))
            )

    query = query.where(*clauses).order_by(
        effective.asc() if sorting == SortOrder.ASCENDING else effective.desc(),
        Entry.id.asc() if sorting == SortOrder.ASCENDING else Entry.id.desc(),
    )
    return query.limit(limit) if limit is not None else query


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
        user_id: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        include: Filtering | None = None,
        exclude: Filtering | None = None,
        sorting: SortOrder = SortOrder.DESCENDING,
        cursor: tuple[int, int] | None = None,
        limit: int | None = None,
    ) -> list[Entry]:
        """列出某个源下的条目，过滤、排序、分页和另外三个流一样都在 SQL 里做完。

        user_id 只有 include/exclude 用得上（已读/收藏是用户维度的）。不传就只按源和
        时间范围取——prune 那类「要这个源的全部条目」的调用方不需要它。
        返回结果是按有效时间排序的（以前是 rowid 顺序），调用方不要依赖它。
        """
        if user_id is None and (include is not None or exclude is not None):
            raise ValueError("要按已读/收藏过滤就必须提供 user_id")
        _logger.debug(
            "获取订阅 %d 的条目，user: %s, start: %s, end: %s, include: %s, "
            "exclude: %s, sorting: %s, cursor: %s, limit: %s",
            feed_id,
            user_id,
            start,
            end,
            include,
            exclude,
            sorting,
            cursor,
            limit,
        )
        query = select(Entry).where(Entry.feed_id == feed_id)
        where_clause = (
            _state_filters(user_id, include, exclude) if user_id is not None else []
        )
        query = _stream_query(
            query,
            where_clause,
            start=start,
            end=end,
            cursor=cursor,
            sorting=sorting,
            limit=limit,
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

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
        """列出用户所有订阅里的条目，过滤、排序、分页都在 SQL 里做完。

        folder_id 只列某个文件夹下的订阅，不传就是全部订阅（文件夹流用它）。
        cursor 是 (秒级时间戳, 条目 id)，与 api/stream.py 的 continuation 同一格式。
        """
        _logger.debug(
            "获取用户 %d 所有订阅源的条目，start: %s, end: %s, include: %s, exclude: %s, sorting: %s, cursor: %s, limit: %s, folder_id: %s",
            user_id,
            start,
            end,
            include,
            exclude,
            sorting,
            cursor,
            limit,
            folder_id,
        )
        query = select(Entry).join(Subscription, Entry.feed_id == Subscription.feed_id)

        where_clause = [
            Subscription.user_id == user_id,
            *_state_filters(user_id, include, exclude),
        ]
        if folder_id is not None:
            where_clause.append(Subscription.folder_id == folder_id)

        query = _stream_query(
            query,
            where_clause,
            start=start,
            end=end,
            cursor=cursor,
            sorting=sorting,
            limit=limit,
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

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
        """列出用户已收藏的条目。

        tag_id 只看某个标签下的收藏；uncategorized 只看未分类的收藏。
        和 list_reading_list 的区别：不按订阅过滤 —— 退订之后收藏仍然留在这里。
        """
        _logger.debug(
            "列出用户 %d 的已加星条目，start: %s, end: %s, include: %s, exclude: %s, sorting: %s, cursor: %s, limit: %s, tag_id: %d, uncategorized: %s",
            user_id,
            start,
            end,
            include,
            exclude,
            sorting,
            cursor,
            limit,
            tag_id if tag_id is not None else -1,
            uncategorized,
        )

        query = select(Entry)
        # 用 EXISTS 而不是 JOIN StarState：JOIN 会让 SQLite 从 star_states 驱动，
        # 取完整个收藏集再排序（实测：1 万条收藏时 10ms，10 万条时 40ms+），
        # 而且**没有任何索引能消除那个排序**（排序键在 entries，过滤维度在
        # star_states，两者靠 join 连接）。EXISTS 则按 ix_entries_page 的顺序扫
        # entries、凑够 LIMIT 就停（0.3ms）。代价是「几乎不收藏」时要把 entries
        # 扫完（10 万条约 11ms）——但那是这个形态自己的最坏值，比 JOIN 那个
        # 无上界的最坏值更可控，两者本身也是同一个量级。
        where_clause = [_has_starred(user_id, tag_id, uncategorized)]

        _read = _has_read(user_id)
        if include == Filtering.READ or exclude == Filtering.UNREAD:
            where_clause.append(_read)
        if include == Filtering.UNREAD or exclude == Filtering.READ:
            where_clause.append(~_read)
        if exclude == Filtering.STARRED:
            # 在收藏流里排除收藏，结果必然是空
            return []

        query = _stream_query(
            query,
            where_clause,
            start=start,
            end=end,
            cursor=cursor,
            sorting=sorting,
            limit=limit,
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

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
        """列出用户已读的条目。

        history=True 只留「真正读过」的（ReadState.timestamp 非空）：批量标已读写的是
        timestamp=None 的 ReadState，那类算「已读」但不算读过，见 ReadState.timestamp
        的注释和 docs/API.md。

        和 list_reading_list 的区别：不按订阅过滤 —— 退订之后读过的条目仍然留在这里。
        cursor 是 (秒级时间戳, 条目 id)，与 api/stream.py 的 continuation 同一格式。
        """
        _logger.debug(
            "列出用户 %d 的已读条目，start: %s, end: %s, include: %s, exclude: %s, sorting: %s, cursor: %s, limit: %s, history: %s",
            user_id,
            start,
            end,
            include,
            exclude,
            sorting,
            cursor,
            limit,
            history,
        )

        query = select(Entry)
        # 同 list_starred：已读集天然很大（重度用户 80% 已读时 JOIN 要 42ms），
        # EXISTS 按 ix_entries_page 有序扫、凑够 LIMIT 就停（0.3ms）
        where_clause = [_has_read(user_id, history)]

        if include == Filtering.UNREAD or exclude == Filtering.READ:
            # 已经隐含「已读」，再要未读（或排除已读）只能是空
            return []

        _starred = _has_starred(user_id)
        if include == Filtering.STARRED:
            where_clause.append(_starred)
        if exclude == Filtering.STARRED:
            where_clause.append(~_starred)

        query = _stream_query(
            query,
            where_clause,
            start=start,
            end=end,
            cursor=cursor,
            sorting=sorting,
            limit=limit,
        )
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
            select(Entry).from_statement(
                text(
                    "SELECT e.* FROM entries e "
                    "JOIN entry_fts ON e.id = entry_fts.rowid "
                    "WHERE entry_fts MATCH :query "
                    "ORDER BY rank "
                    "LIMIT :limit OFFSET :offset"
                )
            ),
            {"query": query, "limit": limit, "offset": offset},
        )
        return list(result.scalars().all())
