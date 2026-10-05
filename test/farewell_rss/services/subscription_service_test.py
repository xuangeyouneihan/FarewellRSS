"""`SubscriptionService.subscribe()` 的两条路径

- 交互式订阅（quickadd / subscription edit）：`fetch=True`，立刻抓一次拿标题，抓不到
  就拒绝订阅 —— 用户该当场看到失败。
- 导入 OPML：`fetch=False`，**只建记录、不抓内容**（对齐 FreshRSS）。一个源 403/503
  不该让整份导入少一条订阅，导入也不必串行等几十次网络；内容交给导入之后的那轮刷新
  （占位记录的 `fetched` 是 `NEVER_FETCHED`，必然被挑中）。
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import NEVER_FETCHED, Base, Feed, Subscription, User
from farewell_rss.factory import build_services
from farewell_rss.feed_fetcher.feed_fetcher import (
    FetchedEntry,
    FetchedFeed,
    FetchError,
)
from farewell_rss.services import feed as feed_service_module


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS entry_fts USING fts5("
                "title, content_plain, summary_plain, "
                "tokenize='trigram', content='entries', content_rowid='id')"
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _add_user(session) -> User:
    user = User(username="u", password_hash="h")
    session.add(user)
    await session.commit()
    return user


def _explode_fetch():
    """任何抓取都算 bug 的假 fetch"""

    async def _fetch(url, etag=None, modified=None):
        raise AssertionError("这条路径不该抓取")

    return _fetch


async def test_subscribe_without_fetch_creates_stub(session, monkeypatch):
    """`fetch=False`：只建记录、绝不抓取，占位 `fetched` = NEVER_FETCHED"""
    monkeypatch.setattr(feed_service_module, "fetch", _explode_fetch())
    user = await _add_user(session)

    subscription = await build_services(session).subscription.subscribe(
        user=user,
        feed_href="https://example.com/a.xml",
        title="OPML 里的标题",
        fetch=False,
    )

    feed = await session.scalar(select(Feed).where(Feed.id == subscription.feed_id))
    assert feed is not None
    assert feed.fetched == NEVER_FETCHED  # 还没抓过 → 下一轮刷新必然挑中它
    assert feed.title is None  # 没抓就没有源标题
    assert subscription.title == "OPML 里的标题"  # 界面上仍显示 OPML 里的标题


async def test_subscribe_without_fetch_reuses_existing_feed(session, monkeypatch):
    """同一个 URL 导入两次：复用同一行源，不重复插"""
    monkeypatch.setattr(feed_service_module, "fetch", _explode_fetch())
    user = await _add_user(session)
    services = build_services(session)

    first = await services.subscription.subscribe(
        user=user, feed_href="https://example.com/a.xml", title="第一次", fetch=False
    )
    second = await services.subscription.subscribe(
        user=user, feed_href="https://example.com/a.xml", title="第二次", fetch=False
    )

    assert first.feed_id == second.feed_id
    assert len((await session.scalars(select(Feed))).all()) == 1


async def test_subscribe_without_fetch_rejects_non_http_url(session, monkeypatch):
    """URL 不像话就不入库

    否则会种下一条永远抓不上、每轮刷新都重试并记警告的订阅 —— 那比少一条订阅更糟。
    """
    monkeypatch.setattr(feed_service_module, "fetch", _explode_fetch())
    user = await _add_user(session)

    with pytest.raises(ValueError, match="不是可抓取的 URL"):
        await build_services(session).subscription.subscribe(
            user=user, feed_href="不是网址", fetch=False
        )

    assert (await session.scalars(select(Feed))).all() == []
    assert (await session.scalars(select(Subscription))).all() == []


async def test_subscribe_with_fetch_uses_fetched_title(session, monkeypatch):
    """默认路径（交互式）：照旧立刻抓一次，标题与时间戳来自抓取结果"""
    fetched_at = datetime.now(UTC)

    async def _fetch(url, etag=None, modified=None):
        return FetchedFeed(
            href=url,
            title="抓来的标题",
            fetched=fetched_at,
            entries=[FetchedEntry(guid="g1", title="t")],
        )

    monkeypatch.setattr(feed_service_module, "fetch", _fetch)
    user = await _add_user(session)

    subscription = await build_services(session).subscription.subscribe(
        user=user, feed_href="https://example.com/a.xml", title="用户填的标题"
    )

    feed = await session.scalar(select(Feed).where(Feed.id == subscription.feed_id))
    assert feed is not None
    assert feed.title == "抓来的标题"
    assert feed.fetched == fetched_at


async def test_subscribe_with_fetch_rejects_unreachable_feed(session, monkeypatch):
    """交互式订阅抓不到就拒绝，不留半个订阅"""

    async def _fail(url, etag=None, modified=None):
        raise FetchError(url)

    monkeypatch.setattr(feed_service_module, "fetch", _fail)
    user = await _add_user(session)

    with pytest.raises(ValueError, match="无法通过 href 插入或获取订阅源"):
        await build_services(session).subscription.subscribe(
            user=user, feed_href="https://example.com/x.xml"
        )

    assert (await session.scalars(select(Subscription))).all() == []
