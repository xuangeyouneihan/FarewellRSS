import logging
from datetime import datetime

from sqlalchemy import Select, and_, delete, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...enums import Filtering, SortOrder
from ...feed_fetcher.feed_fetcher import FetchedEntry
from ..models import Enclosure, Entry, ReadState, StarState, Subscription
from ._chunking import chunked
from .enclosure import EnclosureRepository

_logger = logging.getLogger(__name__)

MIN_TRIGRAM_QUERY = 3
"""FTS5 的 trigram 分词器只索引 3 字 gram，短于 3 个字符的词在索引里根本不存在。

实测（条目标题「异步编程 协程」）：`协`/`协程`/`编程`/`异步` 都是 0 条，`异步编`
（3 字）起才有结果；而且**整串长度不算数**，要看词 —— `异步 编程`（5 个字符）
照样 0 条。所以短词查询只能退回 LIKE 逐条比对。
"""

_FTS_SYNTAX = ('"', "*", "(", ")", ":", "^")
"""带这些字符的查询按 FTS5 语法解释（短语/前缀/括号/列限定/行首锚），不猜它的意思"""

_FTS_OPERATORS = {"AND", "OR", "NOT"}


def short_query_tokens(query: str) -> list[str] | None:
    """纯短词查询 → 返回词表（该退回 LIKE 兜底）；其它一律 None（交给 FTS5）

    会退回的只有「朴素查询且每个词都短于 3 个字符」：`编程`、`炒饭 火候`。
    带 FTS 语法的不退（`"炒饭 火候"` 短语、`协程 OR 编程` 布尔、`编程*` 前缀、
    `title:编程` 列限定）—— 这些查询的意思只有 FTS5 说得清，而索引里既然没有
    那些短词的 gram，它们就是 0 条，不许用 LIKE 猜出一个像是对的结果。
    """
    if any(char in query for char in _FTS_SYNTAX):
        return None
    tokens = query.split()
    if not tokens or any(token.upper() in _FTS_OPERATORS for token in tokens):
        return None
    if all(len(token) < MIN_TRIGRAM_QUERY for token in tokens):
        return tokens
    return None


def _escape_like(value: str) -> str:
    """转义 LIKE 的通配符（调用处用 escape 参数指定反斜杠）

    不转义的话，搜 `_` 会变成「任意一个字符」、搜 `%` 会把整个库倒出来。
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _matches_token(token: str):
    """该词在 title / content_plain / summary_plain 任一列里出现（和 FTS 索引的列一致）"""
    pattern = f"%{_escape_like(token)}%"
    return or_(
        Entry.title.like(pattern, escape="\\"),
        Entry.content_plain.like(pattern, escape="\\"),
        Entry.summary_plain.like(pattern, escape="\\"),
    )


def _subscribed_feed_ids(user_id: int):
    """该用户当前订阅的源 id —— 搜索的范围

    `entries` 是所有用户共用的表（源也是共享的），搜索不带这个条件就等于「谁都能搜到
    别人订阅源里的文章」。语义对齐 reading-list：只看**当前**订阅，不追旧订阅。

    FTS5 那条路没法复用这个表达式（要 JOIN 出 `entry_fts` 才用得上 `rank`，所以那里是
    原生 SQL），在那边写的是等价的一行子查询 —— 两条路都有测试钉着范围。
    """
    return select(Subscription.feed_id).where(Subscription.user_id == user_id)


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
        entries: dict[int, Entry] = {}
        for batch in chunked(ids):
            result = await self._session.execute(
                select(Entry).where(Entry.id.in_(batch))
            )
            entries.update((entry.id, entry) for entry in result.scalars().all())
        _logger.debug("批量获取 %d 条条目，获取到 %d 条", len(ids), len(entries))
        return entries

    async def list_enclosures(self, entry_ids: list[int]) -> dict[int, list[Enclosure]]:
        """一次取多个条目的附件。

        附件是 upsert 时通过 `_enclosure_repository` 写进去的，读也从这里出去 ——
        服务层不必自己再开一个 EnclosureRepository（同一个 session 上出现两个实例，
        视图不一致时很难查）。
        """
        return await self._enclosure_repository.list_by_entries(entry_ids)

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
        self, feed_id: int, entries: list[FetchedEntry]
    ) -> list[Entry]:
        """按 guid 把抓来的条目 upsert 进某个源，返回落库后的 Entry 列表。

        先一条 SQL 把这批 guid 里**已存在**的条目取回来（走 uq_entries_feed_guid
        唯一索引），避免逐条 SELECT 的 N+1。注意不能改成「把整个源的条目都加载进
        map」：源里 2 万条、这次只抓来 50 条时那是 350ms，而只查这 50 个 guid 只要
        4ms（实测）。
        """
        results = []

        existing_map: dict[str, Entry] = {}
        for batch in chunked([item.guid for item in entries]):
            rows = await self._session.scalars(
                select(Entry).where(Entry.feed_id == feed_id, Entry.guid.in_(batch))
            )
            existing_map.update((row.guid, row) for row in rows.all())

        for entry in entries:
            result = existing_map.get(entry.guid)
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
                # 同一次抓取里 guid 可能重复（有些源就是会重复列同一条目）：第二条必须
                # 走更新而不是再插一条，否则直接撞 uq_entries_feed_guid。
                existing_map[entry.guid] = result

            results.append(result)

        # 新插入的条目要拿到 id（附件对账按 id 索引），一次 flush 就够——不用每轮都
        # flush。附件必须一次批量对账：逐条调 update_by_entry 是「每条一次 SELECT」
        # 的 N+1（upsert 一个 50 条的源就多 50 条查询）。
        await self._session.flush()
        await self._enclosure_repository.update_by_entries({
            row.id: fetched.enclosures
            for row, fetched in zip(results, entries, strict=True)
        })

        return results

    async def delete_batch(self, entry_ids: list[int]) -> None:
        """批量删除条目及其附件。

        附件、条目各一条 SQL，按 `_chunking.MAX_IDS_PER_STATEMENT` 分批（`IN (...)`
        的参数个数有硬上限，见那个模块），全部落在同一个事务里（提交由 session 的
        拥有者统一做）——中途失败不会留下「附件删了、条目还在」的半成品。逐条
        delete + flush 的写法是 2N 条 SQL、N 次 COMMIT（实测 40 条＝80 条 SQL），
        分批后语句数只跟批次大小有关，跟条目数无关。

        本方法只负责附件，read_states / star_states 不在这里删——那是调用方
        （下面的 prune_by_feed）的前提：只删既没人收藏也没人读过的条目。
        """
        if not entry_ids:
            _logger.debug("没有条目要删除")
            return
        for batch in chunked(entry_ids):
            await self._enclosure_repository.delete_by_entries(batch)
            await self._session.execute(delete(Entry).where(Entry.id.in_(batch)))
        _logger.debug("删除条目 %d 条", len(entry_ids))
        await self._session.flush()

    async def prune_by_feed(self, feed_id: int) -> int:
        """清掉某个源里「没人真读过也没人收藏」的条目，返回该源还剩多少条目。

        保留条件是**跨用户**的：只要还有任何一个用户真读过（ReadState.timestamp 非空）
        或收藏过，条目就留着——它是那个用户的阅读历史。所以这里不能用
        `_has_read(user_id)` / `_has_starred(user_id)` 那对 helper：它们是按单个用户
        过滤的（给流查询用），拿其中一个用户去判会把别人读过/收藏的条目也删掉。

        「标为已读但没真读过」的状态（timestamp 为空）不保护条目，先清掉；这一步必须
        在删条目之前（条目一没就找不到这些状态了）。

        条目和附件交给 delete_batch（分批 + 同一个事务）。返回**剩余条数**而不是幸存
        条目列表：调用方只需知道「空了没有」（0 即空，据此删掉整个源），而把幸存条目
        全加载成 ORM 对象只为回答这个是非题——实测 2 万条全都幸存时要 183ms，
        而 count 只要 1ms。
        """
        await self._session.execute(
            delete(ReadState).where(
                ReadState.entry_id.in_(
                    select(Entry.id).where(Entry.feed_id == feed_id)
                ),
                ReadState.timestamp.is_(None),
            )
        )

        protected = or_(
            exists(
                select(1).where(
                    ReadState.entry_id == Entry.id,
                    ReadState.timestamp.is_not(None),
                )
            ),
            exists(select(1).where(StarState.entry_id == Entry.id)),
        )
        doomed = list(
            (
                await self._session.scalars(
                    select(Entry.id).where(Entry.feed_id == feed_id, ~protected)
                )
            ).all()
        )
        await self.delete_batch(doomed)

        remaining = await self.entry_count(feed_id)
        await self._session.flush()
        _logger.debug(
            "清理订阅 %d 的条目，删除 %d 条，剩 %d 条", feed_id, len(doomed), remaining
        )
        return remaining

    async def entry_count(self, feed_id: int) -> int:
        entries = await self._session.execute(
            select(func.count()).select_from(Entry).where(Entry.feed_id == feed_id)
        )
        result = entries.scalar_one_or_none()
        if not result:
            result = 0
        _logger.debug("订阅 %d 的条目数量为 %d", feed_id, result)
        return result

    async def search(
        self, query: str, user_id: int, limit: int = 20, offset: int = 0
    ) -> list[Entry]:
        """全文搜索：走 FTS5（支持布尔表达式和短语），全是短词的朴素查询退回 LIKE

        两条路都**只搜该用户订阅的源**（见 `_subscribed_feed_ids`）。

        分岔规则在 `short_query_tokens` 里（要按词判，不能只看整串长度：`异步 编程`
        5 个字符，但两个词都短，FTS5 索引里都不存在）。
        """
        tokens = short_query_tokens(query)
        if tokens is not None:
            return await self.search_substring(tokens, user_id, limit, offset)
        _logger.debug("搜索条目: %s", query)
        result = await self._session.execute(
            select(Entry).from_statement(
                text(
                    "SELECT e.* FROM entries e "
                    "JOIN entry_fts ON e.id = entry_fts.rowid "
                    "WHERE entry_fts MATCH :query "
                    "AND e.feed_id IN ("
                    "SELECT feed_id FROM subscriptions WHERE user_id = :user_id"
                    ") "
                    "ORDER BY rank "
                    "LIMIT :limit OFFSET :offset"
                )
            ),
            {
                "query": query,
                "user_id": user_id,
                "limit": limit,
                "offset": offset,
            },
        )
        return list(result.scalars().all())

    async def search_substring(
        self, tokens: list[str], user_id: int, limit: int = 20, offset: int = 0
    ) -> list[Entry]:
        """短词兜底：每个词都要在三列里出现（AND，不要求相邻），按有效时间降序分页

        范围同 `search`（只搜自己订阅的源）—— 兜底这条路也不能漏掉范围条件。

        **是全表扫描**：前导通配符用不上任何索引（好在 `ix_entries_page` 的排序键
        正好是这里的 order_by，排序不用额外代价）。这是「两字词也能搜到」的代价 ——
        FTS5 的 trigram 索引里没有这些词的 gram，短词没有别的走法。

        多词时用 AND 而不是「整串当一个子串」：LIKE 的 `%炒饭 火候%` 要求原样连着
        出现（含那个空格），基本搜不到东西，而用户敲两个词的意思是「都要有」。
        """
        if not tokens:
            return []
        _logger.debug("短词查询走 LIKE 全表扫描: %s", tokens)
        result = await self._session.execute(
            select(Entry)
            .where(Entry.feed_id.in_(_subscribed_feed_ids(user_id)))
            .where(and_(*[_matches_token(token) for token in tokens]))
            .order_by(_effective_seconds().desc(), Entry.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())
