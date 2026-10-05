import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...feed_fetcher.feed_fetcher import FetchedFeed
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
        _logger.debug("按 href 查找订阅源: %s", href)
        return await self._session.scalar(select(Feed).where(Feed.href == href))

    async def list_(self) -> list[Feed]:
        _logger.debug("列出所有订阅源")
        result = await self._session.execute(select(Feed))
        return list(result.scalars().all())

    async def upsert(self, feed: FetchedFeed) -> Feed:
        result = await self.get_by_href(feed.href)
        if result:
            _logger.debug("更新订阅源 %d，href: %s", result.id, feed.href)
            # 更新现有的 Feed
            result.etag = feed.etag if feed.etag else result.etag
            result.modified = feed.modified if feed.modified else result.modified
            result.title = feed.title if feed.title else result.title
            result.link = feed.link if feed.link else result.link
            result.subtitle = feed.subtitle if feed.subtitle else result.subtitle
            result.published = feed.published if feed.published else result.published
            result.updated = feed.updated if feed.updated else result.updated
            result.fetched = feed.fetched
            result.full_fetched = feed.fetched
            result.author_name = feed.author.name if feed.author else result.author_name
            result.author_href = feed.author.href if feed.author else result.author_href
            result.author_email = (
                feed.author.email if feed.author else result.author_email
            )
            result.icon = feed.icon if feed.icon else result.icon
            result.rights = feed.rights if feed.rights else result.rights
            result.tags = (
                str([tag.label or tag.term for tag in feed.tags])
                if feed.tags
                else result.tags
            )
            result.ttl = feed.ttl if feed.ttl is not None else result.ttl
        else:
            _logger.debug("插入订阅源，href: %s", feed.href)
            # 插入新的 Feed
            result = Feed(
                href=feed.href,
                etag=feed.etag,
                modified=feed.modified,
                title=feed.title,
                link=feed.link,
                subtitle=feed.subtitle,
                published=feed.published,
                updated=feed.updated,
                fetched=feed.fetched,
                full_fetched=feed.fetched,
                author_name=feed.author.name if feed.author else None,
                author_href=feed.author.href if feed.author else None,
                author_email=feed.author.email if feed.author else None,
                icon=feed.icon,
                rights=feed.rights,
                tags=str([tag.label or tag.term for tag in feed.tags])
                if feed.tags
                else None,
                ttl=feed.ttl if feed.ttl is not None else None,
            )
            self._session.add(result)
            await self._session.flush()

        await self._entry_repository.upsert_by_feed(result.id, feed.entries)

        await self._session.flush()

        return result

    async def get_or_create_stub(self, href: str, fetched: datetime) -> Feed:
        """按 href 拿源；没有就建一条「还没抓过内容」的占位记录（**不抓取**）

        与 `upsert` 的区别：这里不碰标题/图标/etag/条目 —— 那些都要抓一次才知道，
        而这条路径刻意不抓（导入 OPML 用）。

        `fetched` 由调用方给（服务层传 `NEVER_FETCHED`）而不是写死在这里：测试要能
        换一个非法值来模拟「写库时才发现不对」的真实 flush 失败。
        """
        feed = await self.get_by_href(href)
        if feed:
            _logger.debug("订阅源已存在 %d，复用，href: %s", feed.id, href)
            return feed
        _logger.debug("插入未抓取过的订阅源占位记录，href: %s", href)
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
