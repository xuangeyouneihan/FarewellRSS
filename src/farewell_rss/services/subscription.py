import logging

from ..db.models import Feed, Label, Subscription, User
from ..db.repositories.subscription import SubscriptionRepository
from .feed import FeedService
from .read_state import ReadStateService

_logger = logging.getLogger(__name__)


class SubscriptionService:
    def __init__(
        self,
        repository: SubscriptionRepository,
        feed_service: FeedService,
        read_state_service: ReadStateService,
    ):
        self._repository = repository
        self._feed_service = feed_service
        self._read_state_service = read_state_service

    async def get(self, user: User, feed: Feed) -> Subscription | None:
        return await self._repository.get(user.id, feed.id)

    async def get_batch(self, user: User, feeds: list[Feed]) -> dict[int, Subscription]:
        feed_ids = [feed.id for feed in feeds]
        return await self._repository.get_batch(user.id, feed_ids)

    async def list_by_folder(self, label: Label) -> list[Subscription]:
        return await self._repository.list_by_folder(label.id)

    async def list_by_user(self, user: User) -> list[Subscription]:
        return await self._repository.list_by_user(user.id)

    async def subscribe(
        self,
        user: User,
        feed_href: str,
        title: str | None = None,
        subtitle: str | None = None,
        link: str | None = None,
        icon: bytes | None = None,
        folder_id: int | None = None,
    ) -> Subscription:
        _logger.info(
            "用户 %s（%d）订阅源 %s，自定义标题：%s，副标题：%s，链接：%s，图标：%s，文件夹：%s",
            user.username,
            user.id,
            feed_href,
            title,
            subtitle,
            link,
            icon,
            folder_id,
        )
        feed = await self._feed_service.insert_by_href(feed_href)
        if feed is None:
            raise ValueError(f"无法通过 href 插入或获取订阅源: {feed_href}")
        return await self._repository.upsert(
            user_id=user.id,
            feed_id=feed.id,
            title=title,
            subtitle=subtitle,
            link=link,
            icon=icon,
            folder_id=folder_id,
        )

    async def update(
        self,
        user: User,
        feed: Feed,
        title: str | None = None,
        subtitle: str | None = None,
        link: str | None = None,
        icon: bytes | None = None,
        folder_id: int | None = None,
    ) -> Subscription:
        _logger.info(
            "用户 %s（%d）更新订阅源 %d，自定义标题：%s，副标题：%s，链接：%s，图标：%s，文件夹：%s",
            user.username,
            user.id,
            feed.id,
            title,
            subtitle,
            link,
            icon,
            folder_id,
        )
        return await self._repository.upsert(
            user_id=user.id,
            feed_id=feed.id,
            title=title,
            subtitle=subtitle,
            link=link,
            icon=icon,
            folder_id=folder_id,
        )

    async def clear_folder(self, label: Label) -> None:
        _logger.info("清空文件夹 %s（%d）的订阅关联", label.name, label.id)
        await self._repository.clear_folder(label.id)

    async def unsubscribe(self, subscription: Subscription) -> None:
        """退订

        如果这是最后一个订阅者，该源会成为「孤儿源」，由 scheduler 的
        周期性清理回收（见 scheduler.prune_orphan_feeds）。**不在这里顺手清理**：
        清理是维护任务，不该让退订请求承担它的耗时与破坏性。
        """
        _logger.info("用户 %d 退订源 %d", subscription.user_id, subscription.feed_id)
        feed = await self._feed_service.get(subscription.feed_id)
        if feed:
            # 「标为已读但没真读过」的状态随订阅一起丢弃，真实阅读历史保留
            await self._read_state_service.prune_by_subscription(
                subscription.user_id, subscription.feed_id
            )
        await self._repository.delete(subscription)

    async def delete_by_user(self, user: User) -> None:
        """删除用户的全部订阅（产生的孤儿源由 scheduler 的清理回收）"""
        _logger.info("删除用户 %d 的所有订阅", user.id)
        subscriptions = await self.list_by_user(user)
        for subscription in subscriptions:
            if await self._feed_service.get(subscription.feed_id):
                await self._read_state_service.prune_by_subscription(
                    subscription.user_id, subscription.feed_id
                )
        await self._repository.delete_by_user(user.id)

    async def subscription_count(self, feed: Feed) -> int:
        return await self._repository.subscription_count(feed.id)

    async def subscription_count_batch(self, feed_ids: list[int]) -> dict[int, int]:
        return await self._repository.subscription_count_batch(feed_ids)