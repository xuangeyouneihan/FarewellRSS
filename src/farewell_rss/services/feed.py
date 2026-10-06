import logging
import os
from datetime import UTC, datetime
from urllib.parse import urlparse

from ..db.models import NEVER_FETCHED, Feed
from ..db.repositories.feed import FeedRepository
from ..feed_fetcher.feed_fetcher import (
    FetchError,
    fetch,
    normalize_href,
    redact_credentials,
)
from .entry import EntryService

_logger = logging.getLogger(__name__)

# 假 304 兜底：不管服务端怎么回 304，每这么久至少完整抓一次（默认一天）
_FULL_REFRESH_INTERVAL = int(
    os.getenv("FAREWELL_RSS_FEED_FULL_REFRESH_INTERVAL", "86400")
)


def _is_http_url(href: str) -> bool:
    """能不能当源去抓：只要 http(s) 且带主机名

    占位记录不校验就入库的话，OPML 里的垃圾串会变成一条永远抓不上、每轮刷新都重试
    并记警告的订阅。
    """
    parts = urlparse(href)
    return parts.scheme in ("http", "https") and bool(parts.netloc)


class FeedService:
    def __init__(self, repository: FeedRepository, entry_service: EntryService):
        self._repository = repository
        self._entry_service = entry_service

    async def get(self, id_: int) -> Feed | None:
        return await self._repository.get(id_)

    async def get_batch(self, ids: list[int]) -> dict[int, Feed]:
        return await self._repository.get_batch(ids)

    async def get_by_href(self, href: str) -> Feed | None:
        return await self._repository.get_by_href(href)

    async def list_(self) -> list[Feed]:
        return await self._repository.list_()

    async def insert_by_href(self, href: str) -> Feed | None:
        # 身份先规范化：`p@ss` 与 `p%40ss` 是同一个密码的两种写法，不折叠就会变成两行
        href = normalize_href(href)
        try:
            feed = await fetch(href)
        except FetchError:
            return None
        if feed:
            _logger.info("已从 %s 获取订阅源 %s", redact_credentials(href), feed.title)
            # 身份用**用户提交的 href**（可能带凭据），不是 `feed.href`——那是脱掉凭据、
            # 跟过重定向的地址，拿它当身份会每次都匹配不上、每轮新建一行
            return await self._repository.upsert(feed, href=href)
        # 此处不记日志，因为 feed_fetcher 那里已经记录了日志
        return None

    async def get_or_create_stub(self, href: str) -> Feed:
        """建一条「还没抓过内容」的源记录（**不抓取**）；已存在就直接返回

        给 OPML 导入用：导入不该依赖网络（几十个源串行抓会很慢），也不该因为某个源
        403/503 就少一条订阅。内容交给之后的刷新 —— `NEVER_FETCHED` 早于任何 TTL，
        调度器下一轮必然把它挑出来抓（见 `scheduler.update_all_feeds`）。

        URL 不像话直接报错（由调用方当作该条 outline 失败），免得种下一条永远失败、
        每轮都重试的订阅。
        """
        href = normalize_href(href)
        if not _is_http_url(href):
            raise ValueError(f"不是可抓取的 URL: {href}")
        return await self._repository.get_or_create_stub(href, NEVER_FETCHED)

    async def update(self, feed: Feed) -> Feed | None:
        # 假 304 兜底：弱 ETag（按定义只保证语义等价、不保证字节相同）、秒级
        # Last-Modified（同一秒内改了内容，服务端 mtime_sec <= If-Modified-Since 就回
        # 304，合法实现也会犯）、CDN/反代拿旧对象比对……都会让服务端回一个「内容其实
        # 变了」的 304。而 304 分支只会 touch，于是这个源静默地永远不再更新 —— 周期性
        # 丢掉条件头是唯一能覆盖以上所有原因的兜底，代价是每源每天多一次完整下载。
        full = (
            feed.full_fetched is None
            or (datetime.now(UTC) - feed.full_fetched).total_seconds()
            >= _FULL_REFRESH_INTERVAL
        )
        try:
            if full:
                _logger.info(
                    "订阅源 %s（%s）距上次完整抓取已超过 %d 秒，本次不带条件头全量抓取",
                    feed.title or redact_credentials(feed.href),
                    redact_credentials(feed.href),
                    _FULL_REFRESH_INTERVAL,
                )
                updated_feed = await fetch(feed.href)
            else:
                updated_feed = await fetch(
                    feed.href, etag=feed.etag, modified=feed.modified
                )
        except FetchError:
            # 网络失败：不更新时间戳，下一轮 TTL 后重试
            return None
        if updated_feed:
            _logger.info(
                "已更新订阅源 %s（%s）",
                updated_feed.title,
                redact_credentials(updated_feed.href),
            )
            # **按行**写回（拿对象定位，不用 href 反查）：href 是这一行的身份，不参与
            # 匹配就不可能因为「脱掉凭据 / 跟过重定向」而变成另一行
            return await self._repository.refresh(feed, updated_feed)
        # 304 未修改或没有条目：更新时间戳，避免反复请求
        await self._repository.touch(feed.id, full=full)
        return None

    async def prune(self, feed: Feed) -> Feed | None:
        if not await self._entry_service.prune_by_feed(feed):
            _logger.info(
                "订阅源 %s（%s）已被清理为空，删除该订阅源", feed.title, feed.href
            )
            await self._repository.delete(feed)
            return None
        _logger.debug(
            "订阅源 %s（%s）已被清理但未为空，保留该订阅源", feed.title, feed.href
        )
        return feed
