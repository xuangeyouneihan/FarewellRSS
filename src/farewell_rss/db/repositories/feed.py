import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...feed_fetcher.feed_fetcher import FetchedFeed
from ..models import Feed
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
        result = await self._session.execute(select(Feed).where(Feed.id.in_(ids)))
        feeds = result.scalars().all()
        _logger.debug("获取到 %d 个", len(feeds))
        return {feed.id: feed for feed in feeds}

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

        await self._entry_repository.upsert_by_feed(
            result.id, feed.entries, commit=False
        )

        await self._session.commit()

        return result

    async def delete(self, feed: Feed) -> None:
        _logger.debug("删除订阅源 %d", feed.id)
        await self._session.delete(feed)
        await self._session.commit()

    async def touch(self, id_: int) -> None:
        """更新时间戳，用于 304 未修改时避免重复请求"""
        _logger.debug("更新订阅源 %d 的抓取时间", id_)
        feed = await self.get(id_)
        if feed:
            feed.fetched = datetime.now(datetime.UTC)
            await self._session.commit()
