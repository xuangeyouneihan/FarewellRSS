from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base, Feed
from farewell_rss.db.repositories.feed import FeedRepository
from farewell_rss.feed_fetcher.feed_fetcher import FetchedFeed


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


async def test_get(session):
    repo = FeedRepository(session)
    feed1 = Feed(
        href="https://example.com/feed1.xml",
        title="测试源 1",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    feed2 = Feed(
        href="https://example.com/feed2.xml",
        title="测试源 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add_all([feed1, feed2])
    await session.commit()

    result = await repo.get(feed1.id)
    assert result == feed1


async def test_get_batch(session):
    repo = FeedRepository(session)

    feed1 = Feed(
        href="https://example.com/feed1.xml",
        title="源 1",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    feed2 = Feed(
        href="https://example.com/feed2.xml",
        title="源 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add_all([feed1, feed2])
    await session.commit()

    result = await repo.get_batch([feed1.id, feed2.id])
    assert result == {feed1.id: feed1, feed2.id: feed2}

    assert await repo.get_batch([]) == {}  # 空列表


async def test_get_by_href(session):
    repo = FeedRepository(session)

    feed1 = Feed(
        href="https://example.com/feed1.xml",
        title="测试源 1",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    feed2 = Feed(
        href="https://example.com/feed2.xml",
        title="测试源 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add_all([feed1, feed2])
    await session.commit()

    result = await repo.get_by_href("https://example.com/feed1.xml")
    assert result == feed1


async def test_list_(session):
    repo = FeedRepository(session)

    feed1 = Feed(
        href="https://example.com/1.xml",
        title="源 1",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    feed2 = Feed(
        href="https://example.com/2.xml",
        title="源 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add_all([feed1, feed2])
    await session.commit()

    result = await repo.list_()
    assert set(result) == {feed1, feed2}


async def test_upsert_insert(session):
    """upsert 新 href 应插入新 Feed"""
    repo = FeedRepository(session)

    fetched1 = FetchedFeed(
        href="https://example.com/feed.xml",
        title="原始标题",
        ttl=60,
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    feed = await repo.upsert(fetched1)
    feed_id = feed.id

    assert feed.id is not None
    assert feed.href == "https://example.com/feed.xml"
    assert feed.title == "原始标题"

    # 验证持久化
    result1 = await repo.get_by_href("https://example.com/feed.xml")
    assert result1 == feed

    # 第二次 upsert，title 变了，ttl 不变
    fetched2 = FetchedFeed(
        href="https://example.com/feed.xml",
        title="更新后的标题",
        ttl=None,  # ttl 为 None 时不应覆盖
        fetched=datetime(1970, 1, 2, tzinfo=UTC),
    )
    updated = await repo.upsert(fetched2)
    assert updated.id == feed_id
    assert updated.title == "更新后的标题"
    assert updated.ttl == 60  # 原值保留
    assert updated.fetched == datetime(1970, 1, 2, tzinfo=UTC)


async def test_delete(session):
    repo = FeedRepository(session)

    feed = Feed(
        href="https://example.com/to-delete.xml",
        title="待删除",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(feed)
    await session.commit()

    await repo.delete(feed)

    assert await repo.get_by_href("https://example.com/to-delete.xml") is None


async def test_touch(session):
    """touch 应更新 fetched 时间戳"""
    repo = FeedRepository(session)

    feed = Feed(
        href="https://example.com/touch.xml",
        title="触达测试",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(feed)
    await session.commit()

    before = feed.fetched
    await repo.touch(feed.id)

    from_db = await repo.get(feed.id)
    assert from_db is not None
    assert from_db.fetched > before
