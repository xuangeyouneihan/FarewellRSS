from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base, Entry, Feed, ReadState, User
from farewell_rss.db.repositories.read_state import ReadStateRepository


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
    u = User(username="test", password_hash="hash", friendly_name="测试用户")
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
    repo = ReadStateRepository(session)
    rs = ReadState(user_id=user.id, entry_id=entry.id)
    session.add(rs)
    await session.commit()

    result = await repo.get(user.id, entry.id)
    assert result == rs


async def test_get_batch(session, user, entry):
    repo = ReadStateRepository(session)

    entry2 = Entry(
        feed_id=entry.feed_id,
        guid="guid-2",
        title="文章 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(entry2)
    await session.commit()

    rs1 = ReadState(user_id=user.id, entry_id=entry.id)
    rs2 = ReadState(user_id=user.id, entry_id=entry2.id)
    session.add_all([rs1, rs2])
    await session.commit()

    result = await repo.get_batch(user.id, [entry.id, entry2.id])
    assert result == {entry.id: rs1, entry2.id: rs2}

    assert await repo.get_batch(user.id, []) == {}


async def test_list_by_user(session, user, entry):
    repo = ReadStateRepository(session)

    user2 = User(username="test2", password_hash="hash")
    session.add(user2)
    await session.commit()

    rs1 = ReadState(user_id=user.id, entry_id=entry.id)
    rs2 = ReadState(user_id=user2.id, entry_id=entry.id)
    session.add_all([rs1, rs2])
    await session.commit()

    result = await repo.list_by_user(user.id)
    assert set(result) == {rs1}


async def test_upsert(session, user, entry):
    repo = ReadStateRepository(session)

    # 插入
    ts = datetime(1970, 1, 1, tzinfo=UTC)
    rs = await repo.upsert(user.id, entry.id, timestamp=ts)
    assert rs.user_id == user.id
    assert rs.entry_id == entry.id
    assert rs.timestamp == ts

    # 更新
    ts2 = datetime(1970, 1, 2, tzinfo=UTC)
    rs2 = await repo.upsert(user.id, entry.id, timestamp=ts2)
    assert rs2.user_id == rs.user_id
    assert rs2.timestamp == ts2


async def test_list_by_subscription(session, user, feed):
    """按订阅列出已读状态，多用户多 feed 隔离"""
    repo = ReadStateRepository(session)

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

    rs1 = ReadState(user_id=user.id, entry_id=e1.id)
    rs2 = ReadState(user_id=user.id, entry_id=e2.id)
    rs3 = ReadState(user_id=user2.id, entry_id=e1.id)  # 另一个用户
    session.add_all([rs1, rs2, rs3])
    await session.commit()

    # feed 下有 2 个已读 + 1 个另一用户的
    result = await repo.list_by_subscription(user.id, feed.id)
    assert set(result) == {rs1, rs2}

    # 没有条目的 feed
    feed3 = Feed(
        href="https://example.com/feed3.xml",
        title="空源",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(feed3)
    await session.commit()
    assert await repo.list_by_subscription(user.id, feed3.id) == []


async def test_upsert_batch(session, user, entry):
    repo = ReadStateRepository(session)

    entry2 = Entry(
        feed_id=entry.feed_id,
        guid="guid-2",
        title="文章 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(entry2)
    await session.commit()

    # 先插入一条
    await repo.upsert(user.id, entry.id, timestamp=datetime(1970, 1, 1, tzinfo=UTC))

    # 批量 upsert：更新已有 + 插入新的
    ts = datetime(1970, 1, 2, tzinfo=UTC)
    result = await repo.upsert_batch(user.id, [entry.id, entry2.id], timestamp=ts)

    assert len(result) == 2
    assert result[entry.id].timestamp == ts
    assert result[entry2.id].timestamp == ts
