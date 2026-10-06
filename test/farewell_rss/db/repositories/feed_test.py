from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base, Feed
from farewell_rss.db.repositories import _chunking
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
    # 必须 dispose：否则 aiosqlite 的连接工作线程会在测试的 event loop
    # 关闭之后才去交付结果，报 "RuntimeError: Event loop is closed"
    await engine.dispose()


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


async def test_get_batch(session, monkeypatch):
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

    # 批次小到 1 时每条都是独立的一批，结果必须和上面一模一样
    monkeypatch.setattr(_chunking, "MAX_IDS_PER_STATEMENT", 1)
    assert await repo.get_batch([feed1.id, feed2.id]) == result


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


async def test_upsert_inserts_with_the_given_identity(session):
    """upsert 的身份取**调用方给的 href**，而不是 `fetched.href`

    真实里 `fetched.href` 是脱掉凭据、跟过重定向的地址；拿它当身份，带凭据的源就会
    每轮都匹配不上（每轮新建一行，订阅那行永远是空的）。
    """
    repo = FeedRepository(session)

    fetched = FetchedFeed(
        href="https://cdn.example.com/feed.xml",  # 与身份不同（模拟脱凭据 / 重定向）
        title="原始标题",
        ttl=60,
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    feed = await repo.upsert(fetched, href="https://example.com/feed.xml")

    assert feed.id is not None
    assert feed.href == "https://example.com/feed.xml"
    assert feed.title == "原始标题"
    assert await repo.get_by_href("https://example.com/feed.xml") is feed
    assert await repo.get_by_href("https://cdn.example.com/feed.xml") is None


async def test_upsert_updates_the_same_row(session):
    """同一个 href 再来一次：更新原行；ttl=None 不覆盖原值"""
    repo = FeedRepository(session)
    href = "https://example.com/feed.xml"
    feed = await repo.upsert(
        FetchedFeed(
            href=href,
            title="原始标题",
            ttl=60,
            fetched=datetime(1970, 1, 1, tzinfo=UTC),
        ),
        href=href,
    )

    updated = await repo.upsert(
        FetchedFeed(
            href=href,
            title="更新后的标题",
            ttl=None,  # ttl 为 None 时不应覆盖
            fetched=datetime(1970, 1, 2, tzinfo=UTC),
        ),
        href=href,
    )

    assert updated.id == feed.id
    assert updated.title == "更新后的标题"
    assert updated.ttl == 60  # 原值保留
    assert updated.fetched == datetime(1970, 1, 2, tzinfo=UTC)


async def test_refresh_writes_into_the_given_row(session):
    """refresh：按行写回；`fetched.href` 指向别处也不新建行、也不改身份"""
    repo = FeedRepository(session)
    feed = Feed(
        href="https://example.com/feed.xml",
        title="旧标题",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(feed)
    await session.commit()

    updated = await repo.refresh(
        feed,
        FetchedFeed(
            href="https://cdn.example.com/feed.xml",
            title="新标题",
            fetched=datetime(1970, 1, 2, tzinfo=UTC),
        ),
    )

    assert updated is feed
    assert updated.href == "https://example.com/feed.xml", "身份被改写了"
    assert updated.title == "新标题"
    assert await repo.get_by_href("https://cdn.example.com/feed.xml") is None


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
