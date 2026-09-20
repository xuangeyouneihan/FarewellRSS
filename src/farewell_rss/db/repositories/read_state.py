import logging
from datetime import datetime

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Entry, ReadState, StarState, Subscription
from .entry import EntryRepository

_logger = logging.getLogger(__name__)


class ReadStateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, user_id: int, entry_id: int) -> ReadState | None:
        _logger.debug("获取已读状态 %d/%d", user_id, entry_id)
        return await self._session.get(ReadState, (user_id, entry_id))

    async def get_batch(
        self, user_id: int, entry_ids: list[int]
    ) -> dict[int, ReadState]:
        if not entry_ids:
            _logger.debug("批量获取已读状态，ids 为空")
            return {}
        _logger.debug("批量获取已读状态，用户: %d, %d 条", user_id, len(entry_ids))
        result = await self._session.execute(
            select(ReadState).where(
                ReadState.user_id == user_id, ReadState.entry_id.in_(entry_ids)
            )
        )
        read_states = result.scalars().all()
        return {read_state.entry_id: read_state for read_state in read_states}

    async def list_by_user(self, user_id: int) -> list[ReadState]:
        _logger.debug("列出用户 %d 的所有已读状态", user_id)
        result = await self._session.execute(
            select(ReadState).where(ReadState.user_id == user_id)
        )
        return list(result.scalars().all())

    async def list_by_subscription(self, user_id: int, feed_id: int) -> list[ReadState]:
        _logger.debug("列出订阅 %d/%d 的已读状态", user_id, feed_id)
        entries = await EntryRepository(self._session).list_by_feed(feed_id)
        entry_ids = [entry.id for entry in entries]
        if not entry_ids:
            return []
        result = await self._session.execute(
            select(ReadState).where(
                ReadState.user_id == user_id, ReadState.entry_id.in_(entry_ids)
            )
        )
        return list(result.scalars().all())

    async def list_history(self, user_id: int) -> list[ReadState]:
        """列出用户的阅读历史：只有 timestamp 非空的才算「真正读过」"""
        _logger.debug("列出用户 %d 的阅读历史", user_id)
        result = await self._session.execute(
            select(ReadState).where(
                ReadState.user_id == user_id, ReadState.timestamp.isnot(None)
            )
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        user_id: int,
        entry_id: int,
        timestamp: datetime | None = None,
        commit: bool = True,
    ) -> ReadState:
        # timestamp 为 None 表示标为已读但不显示在历史记录里
        read_state = await self.get(user_id, entry_id)
        if read_state:
            _logger.debug("更新已读状态 %d/%d", user_id, entry_id)
            read_state.timestamp = timestamp
        else:
            _logger.debug("插入已读状态 %d/%d", user_id, entry_id)
            read_state = ReadState(
                user_id=user_id, entry_id=entry_id, timestamp=timestamp
            )
            self._session.add(read_state)

        if commit:
            await self._session.commit()
        else:
            await self._session.flush()

        return read_state

    async def upsert_batch(
        self,
        user_id: int,
        entry_ids: list[int],
        timestamp: datetime | None = None,
    ) -> dict[int, ReadState]:
        """批量标已读，返回 {entry_id: ReadState}"""
        if not entry_ids:
            return {}
        _logger.debug("批量标已读，用户 %d，共 %d 条", user_id, len(entry_ids))
        existing = await self.get_batch(user_id, entry_ids)
        results: dict[int, ReadState] = {}
        for entry_id in entry_ids:
            rs = existing.get(entry_id)
            if rs:
                rs.timestamp = timestamp
            else:
                rs = ReadState(user_id=user_id, entry_id=entry_id, timestamp=timestamp)
                self._session.add(rs)
            results[entry_id] = rs
        await self._session.commit()
        return results

    async def delete(self, user_id: int, entry_id: int) -> None:
        _logger.debug("删除已读状态 %d/%d", user_id, entry_id)
        read_state = await self.get(user_id, entry_id)
        if read_state:
            await self._session.delete(read_state)
            await self._session.commit()

    async def delete_by_user(self, user_id: int) -> None:
        _logger.debug("删除用户 %d 的所有已读状态", user_id)
        await self._session.execute(
            delete(ReadState).where(ReadState.user_id == user_id)
        )
        await self._session.commit()

    async def prune_by_entry(self, entry_id: int) -> None:
        _logger.debug("清理条目 %d 的无时间戳已读状态", entry_id)
        await self._session.execute(
            delete(ReadState).where(
                ReadState.entry_id == entry_id, ReadState.timestamp.is_(None)
            )
        )
        await self._session.commit()

    async def prune_by_subscription(self, user_id: int, feed_id: int) -> None:
        _logger.debug("清理订阅 %d/%d 的无时间戳已读状态", user_id, feed_id)
        entries = await EntryRepository(self._session).list_by_feed(feed_id)
        entry_ids = [entry.id for entry in entries]
        await self._session.execute(
            delete(ReadState).where(
                ReadState.user_id == user_id,
                ReadState.entry_id.in_(entry_ids),
                ReadState.timestamp.is_(None),
            )
        )
        await self._session.commit()

    async def read_count(self, entry_id: int) -> int:
        _logger.debug("查询条目 %d 的已读计数", entry_id)
        result = await self._session.execute(
            select(func.count())
            .select_from(ReadState)
            .where(ReadState.entry_id == entry_id, ReadState.timestamp.isnot(None))
        )
        return result.scalar_one()

    async def read_count_batch(self, entry_ids: list[int]) -> dict[int, int]:
        if not entry_ids:
            _logger.debug("批量已读计数，ids 为空")
            return {}
        _logger.debug("批量已读计数，共 %d 条", len(entry_ids))
        result = await self._session.execute(
            select(ReadState.entry_id, func.count())
            .where(ReadState.entry_id.in_(entry_ids), ReadState.timestamp.isnot(None))
            .group_by(ReadState.entry_id)
        )
        return {row[0]: row[1] for row in result.all()}

    async def unread_by_feed(self, user_id: int) -> dict[int, tuple[int, int]]:
        """按订阅源统计未读：{feed_id: (未读数, 最新未读条目的 id)}

        「未读」= 该源的条目里没有当前用户的已读状态，用 LEFT JOIN 反连接表达。
        只返回未读数 > 0 的源（全已读的源不会出现在结果里）。
        整个统计一条 SQL 完成，不再逐源取回条目。
        """
        _logger.debug("按源统计用户 %d 的未读数", user_id)
        statement = (
            select(
                Subscription.feed_id,
                func.count().label("unread"),
                func.max(Entry.id).label("newest_id"),
            )
            .join(Entry, Entry.feed_id == Subscription.feed_id)
            .outerjoin(
                ReadState,
                and_(
                    ReadState.entry_id == Entry.id,
                    ReadState.user_id == Subscription.user_id,
                ),
            )
            .where(Subscription.user_id == user_id, ReadState.entry_id.is_(None))
            .group_by(Subscription.feed_id)
            .order_by(Subscription.feed_id)
        )
        result = await self._session.execute(statement)
        return {row.feed_id: (row.unread, row.newest_id) for row in result.all()}

    async def unread_by_tag(self, user_id: int) -> dict[int, tuple[int, int]]:
        """按收藏标签统计未读：{tag_id: (未读数, 最新未读条目的 id)}

        只统计**用户仍在订阅的源**里的条目：退订后保留的收藏不计入
        （与旧实现一致）。另外 tag_id 为空的「纯收藏」不算未读来源。
        """
        _logger.debug("按标签统计用户 %d 的未读数", user_id)
        statement = (
            select(
                StarState.tag_id,
                func.count().label("unread"),
                func.max(StarState.entry_id).label("newest_id"),
            )
            .join(Entry, Entry.id == StarState.entry_id)
            .join(
                Subscription,
                and_(
                    Subscription.feed_id == Entry.feed_id,
                    Subscription.user_id == StarState.user_id,
                ),
            )
            .outerjoin(
                ReadState,
                and_(
                    ReadState.entry_id == Entry.id,
                    ReadState.user_id == StarState.user_id,
                ),
            )
            .where(
                StarState.user_id == user_id,
                StarState.tag_id.is_not(None),
                ReadState.entry_id.is_(None),
            )
            .group_by(StarState.tag_id)
            .order_by(StarState.tag_id)
        )
        result = await self._session.execute(statement)
        return {row.tag_id: (row.unread, row.newest_id) for row in result.all()}
