"""`SubscriptionService.subscribe()` 的两条路径

- 交互式订阅（quickadd / subscription edit）：`fetch=True`，立刻抓一次拿标题；**抓取
  失败不等于订阅失败**，按原因分两种：
  * 网络层失败 / 5xx：先建占位记录，内容交给调度器（同 OPML 导入那条路）；
  * 4xx / 抓到了但没有条目：抛 `FeedFetchFailedError`，当场告诉用户。
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
from farewell_rss.services.exceptions import FeedFetchFailedError


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


async def test_subscribe_with_fetch_falls_back_to_stub_on_network_failure(
    session, monkeypatch
):
    """网络层失败（超时/DNS/TLS）**不**拒绝订阅：先建占位记录，内容交给调度器

    实测过的场景：同一个源经代理抓 4.7 秒能成、14 秒超时也出现过（源本身 1 MB）。
    让用户反复手点重试解决不了任何问题，而占位记录的 `NEVER_FETCHED` 早于任何 TTL，
    调度器下一轮必然把它挑出来。
    """

    async def _fail(url, etag=None, modified=None):
        raise FetchError(url)  # 传输层失败 → transient 默认 True

    monkeypatch.setattr(feed_service_module, "fetch", _fail)
    user = await _add_user(session)

    subscription = await build_services(session).subscription.subscribe(
        user=user, feed_href="https://example.com/slow.xml"
    )

    feed = await session.scalar(select(Feed).where(Feed.id == subscription.feed_id))
    assert feed is not None
    assert feed.fetched == NEVER_FETCHED  # 还没抓过 → 下一轮刷新必然挑中它
    assert feed.title is None  # 没抓到就没有源标题（界面上会退回显示 URL）


async def test_subscribe_with_fetch_rejects_client_error(session, monkeypatch):
    """4xx（403/404/410）是「地址错 / 被拒」：重试不会变好，当场报错且不留半个订阅"""

    async def _fail(url, etag=None, modified=None):
        raise FetchError(url, status=404, transient=False)

    monkeypatch.setattr(feed_service_module, "fetch", _fail)
    user = await _add_user(session)

    with pytest.raises(FeedFetchFailedError, match="404"):
        await build_services(session).subscription.subscribe(
            user=user, feed_href="https://example.com/gone.xml"
        )

    assert (await session.scalars(select(Subscription))).all() == []
    assert (await session.scalars(select(Feed))).all() == []


async def test_subscribe_with_fetch_rejects_feed_without_entries(session, monkeypatch):
    """抓到了 2xx 但一条条目都没有：多半这个地址不是 feed，当场报错

    旧实现在这里是 `ValueError` → API 层 500（用户只看到「服务器错误」，没有任何
    可行动的信息）。
    """

    async def _empty(url, etag=None, modified=None):
        return None

    monkeypatch.setattr(feed_service_module, "fetch", _empty)
    user = await _add_user(session)

    with pytest.raises(FeedFetchFailedError, match="没有解析出任何条目"):
        await build_services(session).subscription.subscribe(
            user=user, feed_href="https://example.com/not-a-feed"
        )

    assert (await session.scalars(select(Subscription))).all() == []


async def test_subscribe_rejects_unusable_url_after_network_failure(
    session, monkeypatch
):
    """兜底：网络层失败后要建占位记录时，URL 不像话也报同一类错（而不是 500）"""

    async def _fail(url, etag=None, modified=None):
        raise FetchError(url)

    monkeypatch.setattr(feed_service_module, "fetch", _fail)
    user = await _add_user(session)

    with pytest.raises(FeedFetchFailedError, match="不是可抓取的 URL"):
        await build_services(session).subscription.subscribe(
            user=user, feed_href="不是网址"
        )

    assert (await session.scalars(select(Subscription))).all() == []
