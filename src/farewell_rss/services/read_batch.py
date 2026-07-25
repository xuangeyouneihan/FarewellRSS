"""批量已读操作服务——编排多个 service，不引入循环依赖"""

import logging
from datetime import datetime

from ..db.models import Entry, Label, LabelType, ReadState, Subscription, User
from .entry import EntryService
from .feed import FeedService
from .read_state import ReadStateService
from .star_state import StarStateService
from .subscription import SubscriptionService

_logger = logging.getLogger(__name__)


class ReadBatchService:
    def __init__(
        self,
        read_state_service: ReadStateService,
        entry_service: EntryService,
        feed_service: FeedService,
        subscription_service: SubscriptionService,
        star_state_service: StarStateService,
    ):
        self._read_state_service = read_state_service
        self._entry_service = entry_service
        self._feed_service = feed_service
        self._subscription_service = subscription_service
        self._star_state_service = star_state_service

    async def _filter_unread(self, user: User, entries: list[Entry]) -> list[Entry]:
        """排除已有 ReadState 的条目，避免覆盖已有时间戳"""
        if not entries:
            return []
        read_map = await self._read_state_service.get_batch(user, entries)
        result = [e for e in entries if e.id not in read_map]
        _logger.debug(
            "过滤未读条目，原始 %d 个，过滤后 %d 个", len(entries), len(result)
        )
        return result

    async def insert_by_subscription(
        self,
        subscription: Subscription,
        older_than_id: int | None = None,
        timestamp: datetime | None = None,
    ) -> list[ReadState]:
        feed = await self._feed_service.get(subscription.feed_id)
        if not feed:
            return []
        entries = await self._entry_service.list_by_feed(feed)
        if older_than_id is not None:
            entries = [e for e in entries if e.id <= older_than_id]
        entries = await self._filter_unread(User(id=subscription.user_id), entries)
        _logger.info(
            "用户 %d 批量标记订阅源 %d 的所有条目为已读，目前有 %d 个未读条目",
            subscription.user_id,
            subscription.feed_id,
            len(entries),
        )
        if not entries:
            return []
        return list(
            (
                await self._read_state_service.upsert_batch(
                    User(id=subscription.user_id), entries, timestamp
                )
            ).values()
        )

    async def insert_by_label(
        self,
        label: Label,
        older_than_id: int | None = None,
        timestamp: datetime | None = None,
    ) -> list[ReadState]:
        match label.type:
            case LabelType.FOLDER:
                subscriptions = await self._subscription_service.list_by_folder(label)
                results: list[ReadState] = []
                for sub in subscriptions:
                    results += await self.insert_by_subscription(
                        sub, older_than_id, timestamp
                    )
                _logger.info(
                    "用户 %d 批量标记文件夹 %s（%d）下的所有条目为已读，共 %d 个",
                    label.user_id,
                    label.name,
                    label.id,
                    len(results),
                )
                return results
            case LabelType.TAG:
                star_states = await self._star_state_service.list_by_tag(label.id)
                if older_than_id is not None:
                    star_states = [
                        ss for ss in star_states if ss.entry_id <= older_than_id
                    ]
                results: list[ReadState] = []
                for ss in star_states:
                    entry = await self._entry_service.get(ss.entry_id)
                    if not entry:
                        continue
                    if await self._read_state_service.get(User(id=ss.user_id), entry):
                        continue
                    results.append(
                        await self._read_state_service.upsert(
                            User(id=ss.user_id), entry, timestamp
                        )
                    )
                _logger.info(
                    "用户 %d 批量标记标签 %s（%d）下的所有条目为已读，共 %d 个",
                    label.user_id,
                    label.name,
                    label.id,
                    len(results),
                )
                return results

    async def insert_by_user(
        self,
        user: User,
        older_than_id: int | None = None,
        timestamp: datetime | None = None,
    ) -> list[ReadState]:
        subscriptions = await self._subscription_service.list_by_user(user)
        results: list[ReadState] = []
        for sub in subscriptions:
            results += await self.insert_by_subscription(sub, older_than_id, timestamp)
        _logger.info(
            "用户 %d 批量标记所有条目为已读，共 %d 个", user.id, len(results)
        )
        return results

    async def insert_starred_by_user(
        self,
        user: User,
        older_than_id: int | None = None,
        timestamp: datetime | None = None,
    ) -> list[ReadState]:
        star_states = await self._star_state_service.list_by_user(user)
        if older_than_id is not None:
            star_states = [ss for ss in star_states if ss.entry_id <= older_than_id]
        entries = []
        for ss in star_states:
            entry = await self._entry_service.get(ss.entry_id)
            if entry:
                entries.append(entry)
        entries = await self._filter_unread(user, entries)
        _logger.info(
            "用户 %d 批量标记所有收藏条目为已读，共 %d 个", user.id, len(entries)
        )
        if not entries:
            return []
        return list(
            (
                await self._read_state_service.upsert_batch(user, entries, timestamp)
            ).values()
        )
