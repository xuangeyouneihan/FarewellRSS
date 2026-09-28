"""调度器的孤儿源清理测试"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base, Entry, Feed, ReadState, Subscription, User
from farewell_rss.factory import build_services
from farewell_rss.scheduler.scheduler import prune_orphan_feeds

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


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
    # 必须 dispose：否则 aiosqlite 的连接工作线程会在测试的 event loop
    # 关闭之后才去交付结果，报 "RuntimeError: Event loop is closed"
    await engine.dispose()


async def _add_feed(session, href: str) -> Feed:
    feed = Feed(href=href, title=href, fetched=_EPOCH)
    session.add(feed)
    await session.commit()
    return feed


async def _add_entry(session, feed: Feed, guid: str) -> Entry:
    entry = Entry(feed_id=feed.id, guid=guid, title=guid, fetched=_EPOCH)
    session.add(entry)
    await session.commit()
    return entry


async def test_prune_orphan_feed_without_references(session):
    """没人订阅、也没人读过/收藏的源：条目连同源一起删掉"""
    feed = await _add_feed(session, "https://example.com/orphan.xml")
    await _add_entry(session, feed, "g1")

    deleted = await prune_orphan_feeds(build_services(session))

    assert deleted == [feed.id]
    assert await session.scalar(select(Feed).where(Feed.id == feed.id)) is None
    assert list((await session.scalars(select(Entry))).all()) == []


async def test_prune_keeps_entries_with_read_history(session):
    """孤儿源里被真实阅读历史引用的条目要保留，源随之保留（历史还能看）"""
    user = User(username="u", password_hash="h")
    session.add(user)
    await session.commit()
    feed = await _add_feed(session, "https://example.com/history.xml")
    read_entry = await _add_entry(session, feed, "g1")
    await _add_entry(session, feed, "g2")
    session.add(
        ReadState(
            user_id=user.id,
            entry_id=read_entry.id,
            timestamp=datetime(1970, 1, 2, tzinfo=UTC),
        )
    )
    await session.commit()

    deleted = await prune_orphan_feeds(build_services(session))

    assert deleted == []
    assert [e.id for e in (await session.scalars(select(Entry))).all()] == [
        read_entry.id
    ]
    assert await session.scalar(select(Feed).where(Feed.id == feed.id)) is not None
    assert (
        await session.scalar(
            select(ReadState).where(ReadState.entry_id == read_entry.id)
        )
        is not None
    )


async def test_prune_skips_feed_with_subscribers(session):
    """还有订阅者的源完全不动"""
    user = User(username="u", password_hash="h")
    session.add(user)
    await session.commit()
    feed = await _add_feed(session, "https://example.com/subscribed.xml")
    await _add_entry(session, feed, "g1")
    session.add(Subscription(user_id=user.id, feed_id=feed.id))
    await session.commit()

    assert await prune_orphan_feeds(build_services(session)) == []
    assert await session.scalar(select(Entry)) is not None
    assert await session.scalar(select(Feed).where(Feed.id == feed.id)) is not None


async def test_prune_is_idempotent(session):
    """重复清理同一批孤儿源不报错、不重复上报"""
    feed = await _add_feed(session, "https://example.com/orphan.xml")
    await _add_entry(session, feed, "g1")

    assert await prune_orphan_feeds(build_services(session)) == [feed.id]
    assert await prune_orphan_feeds(build_services(session)) == []


async def test_prune_survives_one_bad_feed(session, monkeypatch):
    """单个源清理出错不能带走整轮清理

    run() 的 while True 没有兜底：异常跑出去这个调度任务就再也不会醒来了，
    所以 per-feed 的异常必须在这里吞掉并记日志（对齐 _update_one 的做法）。
    """
    bad = await _add_feed(session, "https://example.com/bad.xml")
    good = await _add_feed(session, "https://example.com/good.xml")
    await _add_entry(session, bad, "g1")
    await _add_entry(session, good, "g2")
    await session.commit()

    services = build_services(session)
    original_prune = services.feed.prune

    async def flaky_prune(feed):
        if feed.id == bad.id:
            raise RuntimeError("boom")
        return await original_prune(feed)

    monkeypatch.setattr(services.feed, "prune", flaky_prune)

    # 坏源排在前面（先建的），它抛异常后好的那个仍然要被清掉
    assert await prune_orphan_feeds(services) == [good.id]
    assert await session.scalar(select(Feed).where(Feed.id == good.id)) is None
    assert await session.scalar(select(Feed).where(Feed.id == bad.id)) is not None
