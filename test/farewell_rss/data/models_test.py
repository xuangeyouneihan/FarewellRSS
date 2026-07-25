import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base, Entry, Feed, User


@pytest.fixture
async def db():
    """每个测试用独立的临时数据库"""
    engine = create_async_engine("sqlite+aiosqlite:///")  # ← 纯内存，不落盘
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_create_user(db):
    user = User(username="testuser", password_hash="abc123")
    db.add(user)
    await db.commit()

    result = await db.get(User, "testuser")
    assert result is not None
    assert result.username == "testuser"


@pytest.mark.asyncio
async def test_create_feed_and_entry(db):
    feed = Feed(href="https://example.com/feed.xml", title="测试源")
    db.add(feed)
    await db.commit()

    entry = Entry(
        feed_id=feed.id,
        guid="abc-123",
        title="测试文章",
        link="https://example.com/1",
    )
    db.add(entry)
    await db.commit()

    result = await db.get(Entry, entry.id)
    assert result.title == "测试文章"
    assert result.feed_id == feed.id
