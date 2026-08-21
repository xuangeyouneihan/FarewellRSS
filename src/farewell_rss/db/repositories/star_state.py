import logging
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import StarState
from .entry import EntryRepository

_logger = logging.getLogger(__name__)


class StarStateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, user_id: int, entry_id: int) -> StarState | None:
        _logger.debug("获取收藏状态 %d/%d", user_id, entry_id)
        return await self._session.get(StarState, (user_id, entry_id))

    async def get_batch(
        self, user_id: int, entry_ids: list[int]
    ) -> dict[int, StarState]:
        if not entry_ids:
            _logger.debug("批量获取收藏状态，ids 为空")
            return {}
        _logger.debug("批量获取收藏状态，用户: %d, %d 条", user_id, len(entry_ids))
        result = await self._session.execute(
            select(StarState).where(
                StarState.user_id == user_id, StarState.entry_id.in_(entry_ids)
            )
        )
        star_states = result.scalars().all()
        return {star_state.entry_id: star_state for star_state in star_states}

    async def list_by_user(self, user_id: int) -> list[StarState]:
        _logger.debug("列出用户 %d 的所有收藏", user_id)
        result = await self._session.execute(
            select(StarState).where(StarState.user_id == user_id)
        )
        return list(result.scalars().all())

    async def list_by_subscription(self, user_id: int, feed_id: int) -> list[StarState]:
        _logger.debug("列出订阅 %d/%d 的收藏", user_id, feed_id)
        entries = await EntryRepository(self._session).list_by_feed(feed_id)
        entry_ids = [entry.id for entry in entries]
        if not entry_ids:
            return []
        result = await self._session.execute(
            select(StarState).where(
                StarState.user_id == user_id, StarState.entry_id.in_(entry_ids)
            )
        )
        return list(result.scalars().all())

    async def list_by_tag(self, tag_id: int) -> list[StarState]:
        _logger.debug("列出标签 %d 的收藏", tag_id)
        result = await self._session.execute(
            select(StarState).where(StarState.tag_id == tag_id)
        )
        return list(result.scalars().all())

    async def list_uncategorized(self, user_id: int) -> list[StarState]:
        _logger.debug("列出用户 %d 的未分类收藏", user_id)
        result = await self._session.execute(
            select(StarState).where(
                StarState.user_id == user_id, StarState.tag_id.is_(None)
            )
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        user_id: int,
        entry_id: int,
        tag_id: int | None = None,
        timestamp: datetime | None = None,
    ) -> StarState:
        if timestamp is None:
            timestamp = datetime.now(UTC)
        star_state = await self.get(user_id, entry_id)
        if star_state:
            _logger.debug("更新收藏状态 %d/%d", user_id, entry_id)
            star_state.tag_id = tag_id
            star_state.timestamp = timestamp
        else:
            _logger.debug("插入收藏状态 %d/%d", user_id, entry_id)
            star_state = StarState(
                user_id=user_id,
                entry_id=entry_id,
                tag_id=tag_id,
                timestamp=timestamp,
            )
            self._session.add(star_state)
        await self._session.commit()
        return star_state

    async def clear_tag(self, tag_id: int) -> None:
        _logger.debug("清空标签 %d 的收藏关联", tag_id)
        await self._session.execute(
            update(StarState).where(StarState.tag_id == tag_id).values(tag_id=None)
        )
        await self._session.commit()

    async def delete(self, user_id: int, entry_id: int) -> None:
        _logger.debug("删除收藏状态 %d/%d", user_id, entry_id)
        star_state = await self.get(user_id, entry_id)
        if star_state:
            await self._session.delete(star_state)
            await self._session.commit()

    async def delete_by_user(self, user_id: int) -> None:
        _logger.debug("删除用户 %d 的所有收藏状态", user_id)
        await self._session.execute(
            delete(StarState).where(StarState.user_id == user_id)
        )
        await self._session.commit()

    async def star_count(self, entry_id: int) -> int:
        _logger.debug("查询条目 %d 的收藏计数", entry_id)
        result = await self._session.execute(
            select(func.count())
            .select_from(StarState)
            .where(StarState.entry_id == entry_id)
        )
        return result.scalar_one()

    async def star_count_batch(self, entry_ids: list[int]) -> dict[int, int]:
        if not entry_ids:
            _logger.debug("批量查询收藏计数，ids 为空")
            return {}
        _logger.debug("批量查询收藏计数，%d 条", len(entry_ids))
        result = await self._session.execute(
            select(StarState.entry_id, func.count())
            .where(StarState.entry_id.in_(entry_ids))
            .group_by(StarState.entry_id)
        )
        return {row[0]: row[1] for row in result.all()}
