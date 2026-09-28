import logging
from datetime import datetime

from sqlalchemy.exc import OperationalError

from ..db.models import Entry, Feed
from ..db.repositories.entry import EntryRepository
from ..enums import Filtering, SortOrder
from .exceptions import InvalidSearchQueryError

_logger = logging.getLogger(__name__)


def _is_fts5_syntax_error(error: OperationalError) -> bool:
    """这个 OperationalError 是不是「查询串语法不对」

    只能看消息，不能看错误码：SQLite 对 FTS5 的解析错误和「表不存在」都给这一套
    SQLITE_ERROR（实测 code=1、name=SQLITE_ERROR 完全一样），区分不出来。
    措辞按实测的两种取：

    * ``fts5: syntax error near "OR"`` —— 运算符/括号/空表达式的错（AND 后面没东西）
    * ``unterminated string`` —— 引号不闭合（FTS5 自己的措辞里没有 fts5 前缀，所以
      得单独列一条）

    措辞对不上就返回 False：宁可不翻译（退回 500，看得见），也不要把真正的库错误
    （盘满了、索引坏了）谎报成用户打错了查询。
    """
    message = str(error.orig or error)
    return "fts5: syntax error" in message or message.startswith("unterminated string")


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
        """全文搜索

        三种「查询串本身有问题」的情况都在这里拦住，别让它们变成 500：

        * 空串（或只有空白）—— repository 里那条 LIKE 会把 `%%` 当成「匹配所有行」，
          直接把整个库倒出来
        * 语法不对（引号不闭合、运算符用错……）—— SQLite 抛 OperationalError
        * 1~2 个字符的词 —— 不是错，但 FTS5 的 trigram 索引里没有这么短的 gram
          （见 `MIN_TRIGRAM_QUERY`），repository 会退回 LIKE 全表扫描
        """
        if not query.strip():
            raise InvalidSearchQueryError.from_query(query)
        try:
            return await self._repository.search(query, limit, offset)
        except OperationalError as e:
            if _is_fts5_syntax_error(e):
                raise InvalidSearchQueryError.from_query(query) from e
            raise
