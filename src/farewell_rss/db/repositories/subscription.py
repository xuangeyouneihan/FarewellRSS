import logging

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Feed, Subscription
from ._chunking import chunked

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
        subscriptions: dict[int, Subscription] = {}
        for batch in chunked(feed_ids):
            result = await self._session.execute(
                select(Subscription).where(
                    Subscription.user_id == user_id, Subscription.feed_id.in_(batch)
                )
            )
            subscriptions.update((sub.feed_id, sub) for sub in result.scalars().all())
        return subscriptions

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

    async def subscription_count_batch(self, feed_ids: list[int]) -> dict[int, int]:
        """批量查这些源的订阅人数：{feed_id: 人数}。

        对应 SQL：
            SELECT feed_id, count(*) FROM subscriptions
            WHERE feed_id IN (...) GROUP BY feed_id
        （主键是 (user_id, feed_id)，所以一个 feed_id 的 count(*) 就是订阅人数）

        **没人订阅的源不会出现在结果里**（GROUP BY 天然如此），调用方要用
        `.get(feed_id, 0)`，不能直接 `counts[feed_id]`。

        `IN (...)` 的参数个数有硬上限（见 _chunking），所以按批查、把结果并起来。
        """
        if not feed_ids:
            _logger.debug("批量查询订阅人数，ids 为空")
            return {}
        _logger.debug("批量查询 %d 个源的订阅人数", len(feed_ids))
        counts: dict[int, int] = {}
        for batch in chunked(feed_ids):
            result = await self._session.execute(
                select(Subscription.feed_id, func.count())
                .where(Subscription.feed_id.in_(batch))
                .group_by(Subscription.feed_id)
            )
            counts.update((row[0], row[1]) for row in result.all())
        return counts

    async def prune_orphan_subscriptions(self) -> int:
        """删掉指向已不存在源的订阅（幂等），返回删除条数。

        正常路径上不该有这种订阅：删源那边保证「只删没有订阅者的源」。但「数订阅数」
        和「删源」之间是个竞态窗口（用户恰好在那时订阅了这个源），而且 FK 在 SQLite
        默认不强制，所以这里做一次状态扫描兑底——和孤儿源回收同一个思路：
        **用扫描表达状态，而不是在读取路径上顺手修**。
        """
        orphan = ~exists(select(1).where(Feed.id == Subscription.feed_id))
        # 先数再删：AsyncSession.execute() 的返回类型上没有 rowcount，而且这样能
        # 只在真有孤儿时记日志（正常情况下每轮都是 0）
        count = await self._session.scalar(
            select(func.count()).select_from(Subscription).where(orphan)
        )
        if count:
            _logger.info("清理 %d 条指向已不存在源的订阅", count)
        await self._session.execute(delete(Subscription).where(orphan))
        await self._session.commit()
        return count or 0
