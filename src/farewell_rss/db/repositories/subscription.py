import logging

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Subscription

_logger = logging.getLogger(__name__)


class SubscriptionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, user_id: int, feed_id: int) -> Subscription | None:
        _logger.debug("获取订阅 %d/%d", user_id, feed_id)
        return await self._session.get(Subscription, (user_id, feed_id))

    async def get_batch(
        self, user_id: int, feed_ids: list[int]
    ) -> dict[int, Subscription]:
        if not feed_ids:
            _logger.debug("批量获取订阅，ids 为空")
            return {}
        _logger.debug("批量获取订阅，用户: %d, %d 个", user_id, len(feed_ids))
        result = await self._session.execute(
            select(Subscription).where(
                Subscription.user_id == user_id, Subscription.feed_id.in_(feed_ids)
            )
        )
        subscriptions = result.scalars().all()
        return {sub.feed_id: sub for sub in subscriptions}

    async def list_by_folder(self, folder_id: int | None) -> list[Subscription]:
        if folder_id is None:
            return []
        _logger.debug("列出文件夹 %d 的订阅", folder_id)
        result = await self._session.execute(
            select(Subscription).where(Subscription.folder_id == folder_id)
        )
        return list(result.scalars().all())

    async def list_by_user(self, user_id: int) -> list[Subscription]:
        _logger.debug("列出用户 %d 的订阅", user_id)
        result = await self._session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        return list(result.scalars().all())

    async def upsert(
        self,
        user_id: int,
        feed_id: int,
        title: str | None = None,
        subtitle: str | None = None,
        link: str | None = None,
        icon: bytes | None = None,
        folder_id: int | None = None,
    ) -> Subscription:
        subscription = await self.get(user_id, feed_id)
        if subscription:
            _logger.debug("更新订阅 %d/%d", user_id, feed_id)
            subscription.title = title
            subscription.subtitle = subtitle
            subscription.link = link
            subscription.icon = icon
            subscription.folder_id = folder_id
        else:
            _logger.debug("插入订阅 %d/%d", user_id, feed_id)
            subscription = Subscription(
                user_id=user_id,
                feed_id=feed_id,
                title=title,
                subtitle=subtitle,
                link=link,
                icon=icon,
                folder_id=folder_id,
            )
            self._session.add(subscription)
        await self._session.commit()
        return subscription

    async def clear_folder(self, folder_id: int) -> None:
        _logger.debug("清空文件夹 %d 的订阅关联", folder_id)
        await self._session.execute(
            update(Subscription)
            .where(Subscription.folder_id == folder_id)
            .values(folder_id=None)
        )
        await self._session.commit()

    async def delete(self, subscription: Subscription) -> None:
        _logger.debug("删除订阅 %d/%d", subscription.user_id, subscription.feed_id)
        await self._session.delete(subscription)
        await self._session.commit()

    async def delete_by_user(self, user_id: int) -> None:
        _logger.debug("删除用户 %d 的所有订阅", user_id)
        await self._session.execute(
            delete(Subscription).where(Subscription.user_id == user_id)
        )
        await self._session.commit()

    async def subscription_count(self, feed_id: int) -> int:
        _logger.debug("查询订阅源 %d 的订阅人数", feed_id)
        result = await self._session.execute(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.feed_id == feed_id)
        )
        return result.scalar_one()
