import logging
from datetime import datetime

from ..db.models import Entry, ReadState, User
from ..db.repositories.read_state import ReadStateRepository

_logger = logging.getLogger(__name__)


class ReadStateService:
    def __init__(
        self,
        repository: ReadStateRepository,
    ):
        self._repository = repository

    async def get(self, user: User, entry: Entry) -> ReadState | None:
        return await self._repository.get(user.id, entry.id)

    async def get_batch(self, user: User, entries: list[Entry]) -> dict[int, ReadState]:
        entry_ids = [entry.id for entry in entries]
        return await self._repository.get_batch(user.id, entry_ids)

    async def list_by_user(self, user: User) -> list[ReadState]:
        return await self._repository.list_by_user(user.id)

    async def list_by_subscription(self, user_id: int, feed_id: int) -> list[ReadState]:
        return await self._repository.list_by_subscription(user_id, feed_id)

    async def upsert(
        self,
        user: User,
        entry: Entry,
        timestamp: datetime | None = None,
    ) -> ReadState:
        _logger.info(
            "用户 %s（%d）标记条目 %d 为已读，时间戳：%s",
            user.username,
            user.id,
            entry.id,
            timestamp,
        )
        return await self._repository.upsert(user.id, entry.id, timestamp)

    async def upsert_batch(
        self,
        user: User,
        entries: list[Entry],
        timestamp: datetime | None = None,
    ) -> dict[int, ReadState]:
        _logger.info(
            "用户 %s（%d）批量标记 %d 个条目为已读，时间戳：%s",
            user.username,
            user.id,
            len(entries),
            timestamp,
        )
        return await self._repository.upsert_batch(
            user.id, [e.id for e in entries], timestamp
        )

    async def delete(self, read_state: ReadState) -> None:
        _logger.info(
            "用户 %d 标记条目 %d 为未读", read_state.user_id, read_state.entry_id
        )
        await self._repository.delete(read_state.user_id, read_state.entry_id)

    async def delete_by_user(self, user: User) -> None:
        _logger.info("删除用户 %d 的所有已读状态", user.id)
        await self._repository.delete_by_user(user.id)

    async def prune_by_entry(self, entry: Entry) -> None:
        _logger.info("清理条目 %d 的无时间戳已读状态", entry.id)
        await self._repository.prune_by_entry(entry.id)

    async def prune_by_subscription(self, user_id: int, feed_id: int) -> None:
        _logger.info("清理用户 %d 订阅 %d 的无时间戳已读状态", user_id, feed_id)
        await self._repository.prune_by_subscription(user_id, feed_id)

    async def read_count(self, entry: Entry) -> int:
        return await self._repository.read_count(entry.id)

    async def read_count_batch(self, entries: list[Entry]) -> dict[int, int]:
        entry_ids = [entry.id for entry in entries]
        return await self._repository.read_count_batch(entry_ids)
