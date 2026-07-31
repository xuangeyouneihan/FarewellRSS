from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base, Entry, Feed
from farewell_rss.db.repositories.entry import EntryRepository
from farewell_rss.feed_fetcher.feed_fetcher import FetchedEntry


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
        for trigger in (
            (
                "CREATE TRIGGER IF NOT EXISTS entry_fts_ai AFTER INSERT ON entries BEGIN "
                "INSERT INTO entry_fts(rowid, title, content_plain, summary_plain) "
                "VALUES (new.id, new.title, new.content_plain, new.summary_plain); END"
            ),
            (
                "CREATE TRIGGER IF NOT EXISTS entry_fts_ad AFTER DELETE ON entries BEGIN "
                "INSERT INTO entry_fts(entry_fts, rowid, title, content_plain, summary_plain) "
                "VALUES ('delete', old.id, old.title, old.content_plain, old.summary_plain); END"
            ),
            (
                "CREATE TRIGGER IF NOT EXISTS entry_fts_au AFTER UPDATE ON entries BEGIN "
                "INSERT INTO entry_fts(entry_fts, rowid, title, content_plain, summary_plain) "
                "VALUES ('delete', old.id, old.title, old.content_plain, old.summary_plain); "
                "INSERT INTO entry_fts(rowid, title, content_plain, summary_plain) "
                "VALUES (new.id, new.title, new.content_plain, new.summary_plain); END"
            ),
        ):
            await conn.execute(text(trigger))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def feed(session) -> Feed:
    feed = Feed(
        href="https://example.com/feed.xml",
        title="测试源",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(feed)
    await session.commit()
    return feed


@pytest_asyncio.fixture
async def feed_factory(session):
    class _FeedFactory:
        def __init__(self):
            self.counter = 0

        async def __call__(self) -> Feed:
            self.counter += 1
            feed = Feed(
                href=f"https://example.com/feed{self.counter}.xml",
                title=f"测试源 {self.counter}",
                fetched=datetime(1970, 1, 1, tzinfo=UTC),
            )
            session.add(feed)
            await session.commit()
            return feed

    return _FeedFactory()


async def test_get(session, feed):
    repo = EntryRepository(session)

    entry1 = Entry(
        feed_id=feed.id,
        guid="abc-123",
        title="测试文章 1",
        link="https://example.com/1",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    entry2 = Entry(
        feed_id=feed.id,
        guid="abc-456",
        title="测试文章 2",
        link="https://example.com/2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add_all([entry1, entry2])
    await session.commit()

    # 测试获取条目
    result = await repo.get(entry1.id)
    assert result == entry1


async def test_get_batch(session, feed):
    repo = EntryRepository(session)

    entry1 = Entry(
        feed_id=feed.id,
        guid="abc-123",
        title="测试文章 1",
        link="https://example.com/1",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    entry2 = Entry(
        feed_id=feed.id,
        guid="abc-456",
        title="测试文章 2",
        link="https://example.com/2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add_all([entry1, entry2])
    await session.commit()

    result = await repo.get_batch([entry1.id, entry2.id])
    assert result == {entry1.id: entry1, entry2.id: entry2}

    assert await repo.get_batch([]) == {}  # 空列表


async def test_get_by_feed_and_guid(session, feed):
    repo = EntryRepository(session)

    entry1 = Entry(
        feed_id=feed.id,
        guid="abc-123",
        title="测试文章",
        link="https://example.com/1",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    entry2 = Entry(
        feed_id=feed.id,
        guid="abc-456",
        title="测试文章 2",
        link="https://example.com/2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add_all([entry1, entry2])
    await session.commit()

    # 测试通过 feed_id 和 guid 获取条目
    result = await repo.get_by_feed_and_guid(feed.id, "abc-123")
    assert result == entry1


async def test_list_by_feed(session, feed_factory):
    repo = EntryRepository(session)

    # 创建两个订阅源
    feed1 = await feed_factory()
    feed2 = await feed_factory()
    feed3 = await feed_factory()

    # 创建条目
    entry1 = Entry(
        feed_id=feed1.id,
        guid="abc-123",
        title="测试文章 1",
        link="https://example.com/1",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    entry2 = Entry(
        feed_id=feed1.id,
        guid="abc-456",
        title="测试文章 2",
        link="https://example.com/2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    entry3 = Entry(
        feed_id=feed2.id,
        guid="abc-789",
        title="测试文章 3",
        link="https://example.com/3",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add_all([entry1, entry2, entry3])
    await session.commit()

    # 测试获取订阅源的条目
    result = await repo.list_by_feed(feed1.id)
    assert set(result) == {entry1, entry2}

    assert await repo.list_by_feed(feed3.id) == []  # 没有条目的订阅源


async def test_upsert_by_feed(session, feed_factory):
    repo = EntryRepository(session)
    feed1 = await feed_factory()
    feed2 = await feed_factory()

    # 初始条目列表
    initial_entries = [
        {
            "guid": "abc-123",
            "title": "测试文章 1",
            "link": "https://example.com/1",
            "fetched": datetime(1970, 1, 1, tzinfo=UTC),
        },
        {
            "guid": "abc-456",
            "title": "测试文章 2",
            "link": "https://example.com/2",
            "fetched": datetime(1970, 1, 1, tzinfo=UTC),
        },
    ]
    await repo.upsert_by_feed(feed1.id, [FetchedEntry(**e) for e in initial_entries])

    # 验证初始插入
    result1 = await repo.list_by_feed(feed1.id)
    assert len(result1) == 2
    assert await repo.list_by_feed(feed2.id) == []  # feed2 没有条目

    updated_entries = [
        {
            "guid": "abc-123",
            "title": "测试文章 1 更新",
            "link": "https://example.com/1-updated",
            "fetched": datetime(1970, 1, 2, tzinfo=UTC),
        },
        {
            "guid": "abc-789",
            "title": "测试文章 3",
            "link": "https://example.com/3",
            "fetched": datetime(1970, 1, 2, tzinfo=UTC),
        },
    ]
    await repo.upsert_by_feed(feed1.id, [FetchedEntry(**e) for e in updated_entries])

    final_entries = [
        {
            "guid": "abc-123",
            "title": "测试文章 1 更新",
            "link": "https://example.com/1-updated",
            "fetched": datetime(1970, 1, 2, tzinfo=UTC),
        },
        {
            "guid": "abc-456",
            "title": "测试文章 2",
            "link": "https://example.com/2",
            "fetched": datetime(1970, 1, 1, tzinfo=UTC),
        },
        {
            "guid": "abc-789",
            "title": "测试文章 3",
            "link": "https://example.com/3",
            "fetched": datetime(1970, 1, 2, tzinfo=UTC),
        },
    ]

    result2 = await repo.list_by_feed(feed1.id)
    assert len(result2) == 3
    final_entries.sort(key=lambda e: e["guid"])  # 按 guid 排序
    result2.sort(key=lambda e: e.guid)  # 按 guid 排序，确保顺序一致
    for i in range(3):
        assert result2[i].guid == final_entries[i]["guid"]
        assert result2[i].title == final_entries[i]["title"]
        assert result2[i].link == final_entries[i]["link"]
        assert result2[i].fetched == final_entries[i]["fetched"]

    assert await repo.list_by_feed(feed2.id) == []


async def test_delete_batch(session, feed):
    repo = EntryRepository(session)

    entry1 = Entry(
        feed_id=feed.id,
        guid="abc-123",
        title="测试文章 1",
        link="https://example.com/1",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    entry2 = Entry(
        feed_id=feed.id,
        guid="abc-456",
        title="测试文章 2",
        link="https://example.com/2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    entry3 = Entry(
        feed_id=feed.id,
        guid="abc-789",
        title="测试文章 3",
        link="https://example.com/3",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add_all([entry1, entry2, entry3])
    await session.commit()

    # 删除条目
    await repo.delete_batch([entry1, entry2])

    # 验证删除结果
    result = await repo.list_by_feed(feed.id)
    assert set(result) == {entry3}


async def test_entry_count(session, feed_factory):
    repo = EntryRepository(session)
    feed1 = await feed_factory()
    feed2 = await feed_factory()

    entry1 = Entry(
        feed_id=feed1.id,
        guid="abc-123",
        title="测试文章 1",
        link="https://example.com/1",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    entry2 = Entry(
        feed_id=feed1.id,
        guid="abc-456",
        title="测试文章 2",
        link="https://example.com/2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    entry3 = Entry(
        feed_id=feed2.id,
        guid="abc-789",
        title="测试文章 3",
        link="https://example.com/3",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add_all([entry1, entry2, entry3])
    await session.commit()

    count = await repo.entry_count(feed1.id)
    assert count == 2


async def test_search(session, feed):
    """FTS5 搜索应能找到匹配内容"""
    repo = EntryRepository(session)

    entry = Entry(
        feed_id=feed.id,
        guid="search-test",
        title="Python 异步编程指南",
        content_plain="本文介绍 asyncio 的核心概念",
        summary_plain="协程与事件循环",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(entry)
    await session.commit()

    results = await repo.search("异步编程")
    assert len(results) == 1
    assert results[0].id == entry.id

    with pytest.raises(OperationalError):
        await repo.search('"异步编程')
