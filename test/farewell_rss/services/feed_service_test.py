"""FeedService 的抓取策略：条件请求 + 「假 304」的全量兜底

背景：304 不一定等于「内容没变」。弱 ETag 按定义只保证语义等价、不保证字节相同；
Last-Modified 只有秒级精度（同一秒内改内容，服务端 `mtime_sec <= If-Modified-Since`
就回 304，合法实现也会犯）；CDN/反代还会拿旧对象比对。踩上任何一种，源就会静默地
永远不更新 —— 所以这里钉住「周期性丢掉条件头全量抓一次」这条兜底。
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import NEVER_FETCHED, Base, Feed
from farewell_rss.factory import build_services
from farewell_rss.feed_fetcher.feed_fetcher import FetchedEntry, FetchedFeed
from farewell_rss.services import feed as feed_service_module

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ETAG = 'W/"v1"'
_MODIFIED = "Mon, 05 Oct 2026 00:00:00 GMT"


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
    # 必须 dispose：见 scheduler_test 的同名 fixture（否则测试结束后连接线程才交付结果）
    await engine.dispose()


async def _add_feed(
    session,
    *,
    href: str = "https://example.com/feed.xml",
    full_fetched: datetime | None,
) -> Feed:
    feed = Feed(
        href=href,
        title="源",
        fetched=_EPOCH,
        etag=_ETAG,
        modified=_MODIFIED,
        full_fetched=full_fetched,
    )
    session.add(feed)
    await session.commit()
    return feed


def _recording_fetch(result: FetchedFeed | None):
    """假抓取：记录收到的条件头，固定返回 result（None 就是 304/空源）"""
    calls: list[dict] = []

    async def _fetch(url, etag=None, modified=None):
        calls.append({"url": url, "etag": etag, "modified": modified})
        return result

    return _fetch, calls


async def test_uses_conditional_request_when_full_fetch_is_recent(session, monkeypatch):
    """距上次完整抓取还在间隔内：带条件头（etag + modified）"""
    now = datetime.now(UTC)
    feed = await _add_feed(session, full_fetched=now)
    fetch, calls = _recording_fetch(None)  # 假装 304
    monkeypatch.setattr(feed_service_module, "fetch", fetch)

    assert await build_services(session).feed.update(feed) is None

    assert calls == [{"url": feed.href, "etag": _ETAG, "modified": _MODIFIED}]
    # 304 不清验证器、也不前移节流阀：否则兜底会一直被推迟
    assert feed.etag == _ETAG
    assert feed.full_fetched == now


async def test_forces_full_fetch_when_full_fetched_is_none(session, monkeypatch):
    """老库补列后 full_fetched 是 NULL：下一次刷新就全量抓一次"""
    feed = await _add_feed(session, full_fetched=None)
    fetch, calls = _recording_fetch(None)
    monkeypatch.setattr(feed_service_module, "fetch", fetch)

    await build_services(session).feed.update(feed)

    assert calls[0]["etag"] is None
    assert calls[0]["modified"] is None


async def test_forces_full_fetch_when_interval_elapsed(session, monkeypatch):
    """超过间隔：同样不带条件头（这是「假 304」下唯一能自愈的路径）"""
    feed = await _add_feed(session, full_fetched=datetime.now(UTC) - timedelta(days=2))
    fetch, calls = _recording_fetch(None)
    monkeypatch.setattr(feed_service_module, "fetch", fetch)

    await build_services(session).feed.update(feed)

    assert calls[0]["etag"] is None
    assert calls[0]["modified"] is None


async def test_full_fetch_resets_the_throttle(session, monkeypatch):
    """完整抓到 200：写库时把节流阀推到这次抓取时间，下一轮又走条件请求"""
    feed = await _add_feed(session, full_fetched=None)
    fetched_at = datetime.now(UTC)
    result = FetchedFeed(
        href=feed.href,
        title="源",
        fetched=fetched_at,
        entries=[FetchedEntry(guid="g1", title="新条目")],
    )
    fetch, calls = _recording_fetch(result)
    monkeypatch.setattr(feed_service_module, "fetch", fetch)
    services = build_services(session)

    updated = await services.feed.update(feed)

    assert updated is not None
    assert feed.full_fetched == fetched_at

    await services.feed.update(feed)
    assert calls[1]["etag"] == _ETAG, "节流阀没前移，下一轮又全量抓了"


async def test_forced_full_fetch_that_still_304s_advances_the_throttle(
    session, monkeypatch
):
    """强制全量了却还是 304（病态缓存）：节流阀也要前移，否则每轮都全量"""
    feed = await _add_feed(session, full_fetched=None)
    fetch, calls = _recording_fetch(None)
    monkeypatch.setattr(feed_service_module, "fetch", fetch)
    services = build_services(session)

    await services.feed.update(feed)

    assert feed.full_fetched is not None

    await services.feed.update(feed)
    assert calls[1]["etag"] == _ETAG


async def test_insert_by_href_records_a_full_fetch(session, monkeypatch):
    """新源入库时就算一次完整抓取（insert_by_href 本来就不带条件头）"""
    fetched_at = datetime.now(UTC)
    href = "https://example.com/new.xml"
    result = FetchedFeed(href=href, title="新源", fetched=fetched_at)
    fetch, calls = _recording_fetch(result)
    monkeypatch.setattr(feed_service_module, "fetch", fetch)

    feed = await build_services(session).feed.insert_by_href(href)

    assert feed is not None
    assert feed.full_fetched == fetched_at
    assert calls == [{"url": href, "etag": None, "modified": None}]


async def test_get_or_create_stub_creates_placeholder(session, monkeypatch):
    """占位记录（OPML 导入用）：一次都不抓，`fetched` = NEVER_FETCHED

    = 还没抽过内容 → 早于任何 TTL → 调度器下一轮必然把它挑出来抓。
    """
    fetch, calls = _recording_fetch(None)
    monkeypatch.setattr(feed_service_module, "fetch", fetch)

    feed = await build_services(session).feed.get_or_create_stub(
        "https://example.com/new.xml"
    )

    assert calls == []
    assert feed.fetched == NEVER_FETCHED
    assert feed.title is None


async def test_get_or_create_stub_reuses_existing(session, monkeypatch):
    """已经存在的源直接复用（同一个 URL 导入两次不会重复插）"""
    fetch, _ = _recording_fetch(None)
    monkeypatch.setattr(feed_service_module, "fetch", fetch)
    services = build_services(session)

    first = await services.feed.get_or_create_stub("https://example.com/new.xml")
    second = await services.feed.get_or_create_stub("https://example.com/new.xml")

    assert first.id == second.id


async def test_get_or_create_stub_rejects_non_http_url(session, monkeypatch):
    """不是 http(s) 就不入库：否则会种下一条永远抓不上、每轮都重试的订阅"""
    fetch, _ = _recording_fetch(None)
    monkeypatch.setattr(feed_service_module, "fetch", fetch)
    services = build_services(session)

    for bad in ("ftp://example.com/feed.xml", "example.com/feed.xml", ""):
        with pytest.raises(ValueError, match="不是可抓取的 URL"):
            await services.feed.get_or_create_stub(bad)
