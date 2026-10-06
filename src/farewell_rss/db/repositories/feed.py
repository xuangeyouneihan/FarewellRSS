import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...feed_fetcher.feed_fetcher import FetchedFeed, redact_credentials
from ..models import Feed
from ._chunking import chunked
from .entry import EntryRepository

_logger = logging.getLogger(__name__)


class FeedRepository:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._entry_repository = EntryRepository(session)

    async def get(self, id_: int) -> Feed | None:
        _logger.debug("获取订阅源 %d", id_)
        return await self._session.get(Feed, id_)

    async def get_batch(self, ids: list[int]) -> dict[int, Feed]:
        if not ids:
            return {}
        _logger.debug("批量获取 %d 个订阅源", len(ids))
        feeds: dict[int, Feed] = {}
        for batch in chunked(ids):
            result = await self._session.execute(select(Feed).where(Feed.id.in_(batch)))
            feeds.update((feed.id, feed) for feed in result.scalars().all())
        _logger.debug("获取到 %d 个", len(feeds))
        return feeds

    async def get_by_href(self, href: str) -> Feed | None:
        _logger.debug("按 href 查找订阅源: %s", redact_credentials(href))
        return await self._session.scalar(select(Feed).where(Feed.href == href))

    async def list_(self) -> list[Feed]:
        _logger.debug("列出所有订阅源")
        result = await self._session.execute(select(Feed))
        return list(result.scalars().all())

    async def upsert(self, fetched: FetchedFeed, href: str | None = None) -> Feed:
        """按 **href** 写入抓取结果：已有就更新，没有就新建（**订阅路径**）

        `href` 由调用方显式给出，而不是用 `fetched.href`：抓回来的 href 是**脱掉凭据、
        跟过重定向**的地址，拿它当身份会让带凭据的源匹配不上——每轮刷新都新建一行，
        而订阅指着的那一行永远是空的。身份只由「用户提交了什么」决定。
        """
        identity = href or fetched.href
        feed = await self.get_by_href(identity)
        if feed is None:
            # 先建个只有身份的壳，字段统一交给 refresh 填（避免两处各写一遍字段表）
            # 日志一律过 redact_credentials：万一以后有人把这里换成带凭据的身份串，
            # 也不会把凭据写进日志
            _logger.debug("插入订阅源，href: %s", redact_credentials(fetched.href))
            feed = Feed(href=identity, fetched=fetched.fetched)
            self._session.add(feed)
            await self._session.flush()
        return await self.refresh(feed, fetched)

    async def refresh(self, feed: Feed, fetched: FetchedFeed) -> Feed:
        """把抓取结果写回**已知的那一行**（**刷新路径**，按对象定位而不是按 href）

        `href` 是这一行的身份，由创建它的那次订阅决定，这里一律不改它（重定向后的
        最终地址只用于解析相对链接，见 `feed_fetcher.fetch`）。
        """
        _logger.debug(
            "更新订阅源 %d，href: %s", feed.id, redact_credentials(fetched.href)
        )
        feed.etag = fetched.etag if fetched.etag else feed.etag
        feed.modified = fetched.modified if fetched.modified else feed.modified
        feed.title = fetched.title if fetched.title else feed.title
        feed.link = fetched.link if fetched.link else feed.link
        feed.subtitle = fetched.subtitle if fetched.subtitle else feed.subtitle
        feed.published = fetched.published if fetched.published else feed.published
        feed.updated = fetched.updated if fetched.updated else feed.updated
        feed.fetched = fetched.fetched
        feed.full_fetched = fetched.fetched
        feed.author_name = fetched.author.name if fetched.author else feed.author_name
        feed.author_href = fetched.author.href if fetched.author else feed.author_href
        feed.author_email = (
            fetched.author.email if fetched.author else feed.author_email
        )
        feed.icon = fetched.icon if fetched.icon else feed.icon
        feed.rights = fetched.rights if fetched.rights else feed.rights
        feed.tags = (
            str([tag.label or tag.term for tag in fetched.tags])
            if fetched.tags
            else feed.tags
        )
        feed.ttl = fetched.ttl if fetched.ttl is not None else feed.ttl

        await self._entry_repository.upsert_by_feed(feed.id, fetched.entries)

        await self._session.flush()

        return feed

    async def get_or_create_stub(self, href: str, fetched: datetime) -> Feed:
        """按 href 拿源；没有就建一条「还没抓过内容」的占位记录（**不抓取**）

        与 `upsert` 的区别：这里不碰标题/图标/etag/条目 —— 那些都要抓一次才知道，
        而这条路径刻意不抓（导入 OPML 用）。

        `fetched` 由调用方给（服务层传 `NEVER_FETCHED`）而不是写死在这里：测试要能
        换一个非法值来模拟「写库时才发现不对」的真实 flush 失败。
        """
        feed = await self.get_by_href(href)
        if feed:
            _logger.debug(
                "订阅源已存在 %d，复用，href: %s", feed.id, redact_credentials(href)
            )
            return feed
        _logger.debug(
            "插入未抓取过的订阅源占位记录，href: %s", redact_credentials(href)
        )
        feed = Feed(href=href, fetched=fetched)
        self._session.add(feed)
        await self._session.flush()
        return feed

    async def delete(self, feed: Feed) -> None:
        _logger.debug("删除订阅源 %d", feed.id)
        await self._session.delete(feed)
        await self._session.flush()

    async def touch(self, id_: int, *, full: bool = False) -> None:
        """更新时间戳，用于 304 未修改时避免重复请求

        `full=True` 用于「这次是按全量兜底去抓的，但仍然是 304」：那种情况下
        `full_fetched` 也得跟着往前走，否则每个刷新周期都会再强制全量一次。
        """
        _logger.debug("更新订阅源 %d 的抓取时间", id_)
        feed = await self.get(id_)
        if feed:
            feed.fetched = datetime.now(UTC)
            if full:
                feed.full_fetched = feed.fetched
            await self._session.flush()
