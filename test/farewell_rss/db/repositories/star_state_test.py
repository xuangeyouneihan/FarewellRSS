from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base, Entry, Feed, StarState, User
from farewell_rss.db.repositories.star_state import StarStateRepository


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


@pytest_asyncio.fixture
async def user(session) -> User:
    u = User(username="test", password_hash="hash")
    session.add(u)
    await session.commit()
    return u


@pytest_asyncio.fixture
async def feed(session) -> Feed:
    f = Feed(
        href="https://example.com/feed.xml",
        title="测试源",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(f)
    await session.commit()
    return f


@pytest_asyncio.fixture
async def entry(session, feed) -> Entry:
    e = Entry(
        feed_id=feed.id,
        guid="test-guid",
        title="测试文章",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(e)
    await session.commit()
    return e


async def test_get(session, user, entry):
    repo = StarStateRepository(session)
    ss = StarState(
        user_id=user.id, entry_id=entry.id, timestamp=datetime(1970, 1, 1, tzinfo=UTC)
    )
    session.add(ss)
    await session.commit()

    result = await repo.get(user.id, entry.id)
    assert result == ss


async def test_get_batch(session, user, entry):
    repo = StarStateRepository(session)

    entry2 = Entry(
        feed_id=entry.feed_id,
        guid="guid-2",
        title="文章 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(entry2)
    await session.commit()

    ss1 = StarState(
        user_id=user.id, entry_id=entry.id, timestamp=datetime(1970, 1, 1, tzinfo=UTC)
    )
    ss2 = StarState(
        user_id=user.id, entry_id=entry2.id, timestamp=datetime(1970, 1, 1, tzinfo=UTC)
    )
    session.add_all([ss1, ss2])
    await session.commit()

    result = await repo.get_batch(user.id, [entry.id, entry2.id])
    assert result == {entry.id: ss1, entry2.id: ss2}

    assert await repo.get_batch(user.id, []) == {}


async def test_list_by_user(session, user, entry):
    repo = StarStateRepository(session)

    user2 = User(username="test2", password_hash="hash")
    session.add(user2)
    await session.commit()

    ss1 = StarState(
        user_id=user.id, entry_id=entry.id, timestamp=datetime(1970, 1, 1, tzinfo=UTC)
    )
    ss2 = StarState(
        user_id=user2.id, entry_id=entry.id, timestamp=datetime(1970, 1, 1, tzinfo=UTC)
    )
    session.add_all([ss1, ss2])
    await session.commit()

    result = await repo.list_by_user(user.id)
    assert set(result) == {ss1}


async def test_upsert(session, user, entry):
    repo = StarStateRepository(session)

    # 插入
    ts = datetime(1970, 1, 1, tzinfo=UTC)
    ss = await repo.upsert(user.id, entry.id, timestamp=ts)
    assert ss.user_id == user.id
    assert ss.timestamp == ts

    # 更新 tag_id
    ss2 = await repo.upsert(user.id, entry.id, tag_id=42)
    assert ss2.user_id == user.id
    assert ss2.tag_id == 42


async def test_delete(session, user, entry):
    repo = StarStateRepository(session)

    ss = StarState(
        user_id=user.id, entry_id=entry.id, timestamp=datetime(1970, 1, 1, tzinfo=UTC)
    )
    session.add(ss)
    await session.commit()

    await repo.delete(user.id, entry.id)

    assert await repo.get(user.id, entry.id) is None


async def test_list_by_subscription(session, user, feed):
    """按订阅列出收藏，多用户多 feed 隔离"""
    repo = StarStateRepository(session)

    feed2 = Feed(
        href="https://example.com/feed2.xml",
        title="源 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(feed2)
    await session.commit()

    e1 = Entry(
        feed_id=feed.id,
        guid="g1",
        title="文章 1",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    e2 = Entry(
        feed_id=feed.id,
        guid="g2",
        title="文章 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    e3 = Entry(
        feed_id=feed2.id,
        guid="g3",
        title="文章 3",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add_all([e1, e2, e3])
    await session.commit()

    user2 = User(username="test2", password_hash="hash")
    session.add(user2)
    await session.commit()

    ss1 = StarState(
        user_id=user.id, entry_id=e1.id, timestamp=datetime(1970, 1, 1, tzinfo=UTC)
    )
    ss2 = StarState(
        user_id=user.id, entry_id=e2.id, timestamp=datetime(1970, 1, 1, tzinfo=UTC)
    )
    ss3 = StarState(
        user_id=user2.id, entry_id=e1.id, timestamp=datetime(1970, 1, 1, tzinfo=UTC)
    )
    session.add_all([ss1, ss2, ss3])
    await session.commit()

    result = await repo.list_by_subscription(user.id, feed.id)
    assert set(result) == {ss1, ss2}
