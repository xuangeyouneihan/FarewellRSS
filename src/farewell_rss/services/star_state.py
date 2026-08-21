import logging
from datetime import datetime

from ..db.models import Entry, Label, StarState, User
from ..db.repositories.star_state import StarStateRepository

_logger = logging.getLogger(__name__)


class StarStateService:
    def __init__(self, repository: StarStateRepository):
        self._repository = repository

    async def get(self, user: User, entry: Entry) -> StarState | None:
        return await self._repository.get(user.id, entry.id)

    async def get_batch(self, user: User, entries: list[Entry]) -> dict[int, StarState]:
        entry_ids = [entry.id for entry in entries]
        return await self._repository.get_batch(user.id, entry_ids)

    async def list_by_user(self, user: User) -> list[StarState]:
        return await self._repository.list_by_user(user.id)

    async def list_by_subscription(self, user_id: int, feed_id: int) -> list[StarState]:
        return await self._repository.list_by_subscription(user_id, feed_id)

    async def list_by_tag(self, tag_id: int) -> list[StarState]:
        return await self._repository.list_by_tag(tag_id)

    async def list_uncategorized(self, user: User) -> list[StarState]:
        return await self._repository.list_uncategorized(user.id)

    async def upsert(
        self,
        user: User,
        entry: Entry,
        tag_id: int | None = None,
        timestamp: datetime | None = None,
    ) -> StarState:
        _logger.info(
            "用户 %s（%d）收藏条目 %d，标签：%d，时间戳：%s",
            user.username,
            user.id,
            entry.id,
            tag_id,
            timestamp,
        )
        return await self._repository.upsert(user.id, entry.id, tag_id, timestamp)

    async def delete(self, star_state: StarState) -> None:
        _logger.info("用户 %d 取消收藏条目 %d", star_state.user_id, star_state.entry_id)
        await self._repository.delete(star_state.user_id, star_state.entry_id)

    async def delete_by_user(self, user: User) -> None:
        _logger.info("删除用户 %d 的所有收藏状态", user.id)
        await self._repository.delete_by_user(user.id)

    async def star_count(self, entry: Entry) -> int:
        return await self._repository.star_count(entry.id)

    async def clear_tag(self, label: Label) -> None:
        _logger.info("将标签 %d 从所有收藏状态中移除", label.id)
        await self._repository.clear_tag(label.id)

    async def star_count_batch(self, entries: list[Entry]) -> dict[int, int]:
        entry_ids = [entry.id for entry in entries]
        return await self._repository.star_count_batch(entry_ids)
