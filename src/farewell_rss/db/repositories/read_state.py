import logging
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ReadState
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
