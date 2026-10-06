import logging

from ..db.models import Feed, Label, Subscription, User
from ..db.repositories.subscription import SubscriptionRepository
from ..feed_fetcher.feed_fetcher import FetchError, redact_credentials
from .exceptions import FeedFetchFailedError
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
        fetch: bool = True,
    ) -> Subscription:
        """订阅一个源

        `fetch=True`（默认）：立刻抓一次拿标题/图标 —— 交互式订阅（quickadd /
        subscription edit）走这条。但**抓取失败不等于订阅失败**，按失败原因分两种：

        * 网络层失败（超时/DNS/TLS）与 5xx/429：上游「现在不行」，先把订阅建起来
          （占位记录，与 OPML 导入同一条路），内容交给下一轮刷新。交互式订阅被一次
          超时卡住是错的：同一条链路几秒前能成、十几秒后超时都可能（代理不稳、源
          本身慢），让用户反复手点重试解决不了任何问题。
        * 4xx（403 被 WAF 拒 / 404 源已失效 / 410 已删）与「抓到了但没有条目」：
          那是**地址不对或源已废**，重试不会变好 —— 抛 `FeedFetchFailedError`，
          当场告诉用户。

        `fetch=False`：只建记录、不抓内容 —— 导入 OPML 走这条（对齐 FreshRSS：它导入
        时只建订阅，抓取交给之后的刷新）。一个源 403/503 不该让整份导入少一条订阅，
        导入也不必串行等几十次网络；内容交给下一轮刷新（占位记录的 `fetched` 是
        `NEVER_FETCHED`，必然被挑中）。
        """
        _logger.info(
            "用户 %s（%d）订阅源 %s，自定义标题：%s，副标题：%s，链接：%s，图标：%s，文件夹：%s，fetch：%s",
            user.username,
            user.id,
            redact_credentials(feed_href),
            title,
            subtitle,
            link,
            icon,
            folder_id,
            fetch,
        )
        feed = (
            await self._fetched_feed(feed_href)
            if fetch
            else await self._feed_service.get_or_create_stub(feed_href)
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

    async def _fetched_feed(self, feed_href: str) -> Feed:
        """抓一次拿源记录；抓不到时按「值不值得重试」决定是报错还是先建占位记录"""
        try:
            feed = await self._feed_service.insert_by_href(feed_href)
        except FetchError as error:
            if not error.transient:
                raise FeedFetchFailedError(
                    f"无法订阅 {redact_credentials(feed_href)}：对方返回 {error.reason}"
                ) from error
            _logger.warning(
                "订阅源 %s 首次抓取失败（%s），先建订阅，内容交给下一轮刷新",
                redact_credentials(feed_href),
                error.reason,
            )
            return await self._stub(feed_href)
        if feed is None:
            # 2xx 但一条条目都没有：多半这个地址根本不是 feed（WAF 挑战页 / HTML 页面），
            # 也可能是个暂时空的真源。两种都当场说清楚：此处不能返回 None（旧实现在
            # 这里抛出 ValueError，最终变成一个没有任何解释的 500）。
            raise FeedFetchFailedError(
                f"无法订阅 {redact_credentials(feed_href)}："
                "这个地址没有解析出任何条目，可能不是 RSS 源"
            )
        return feed

    async def _stub(self, feed_href: str) -> Feed:
        """建一条「还没抓过内容」的源记录（不抓）

        `get_or_create_stub` 会拒绍不像话的 URL（`_is_http_url`）並抛 `ValueError`。
        正常情况走不到（能过 `fetch` 的网络层失败就说明地址能解析），这里只是兵兵：
        宁可报一句能看懂的错，也别再让人看见 500。
        """
        try:
            return await self._feed_service.get_or_create_stub(feed_href)
        except ValueError as error:
            raise FeedFetchFailedError(
                f"无法订阅 {redact_credentials(feed_href)}：{error}"
            ) from error

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

    async def prune_orphan_subscriptions(self) -> int:
        """删掉指向已不存在源的订阅（幂等），返回删除条数；由 scheduler 每轮调"""
        return await self._repository.prune_orphan_subscriptions()
