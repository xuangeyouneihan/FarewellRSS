from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base, Feed, Label, LabelType, Subscription, User
from farewell_rss.db.repositories.subscription import SubscriptionRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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


async def test_get(session, user, feed):
    repo = SubscriptionRepository(session)
    sub = Subscription(user_id=user.id, feed_id=feed.id)
    session.add(sub)
    await session.commit()

    result = await repo.get(user.id, feed.id)
    assert result == sub


async def test_get_batch(session, user, feed):
    repo = SubscriptionRepository(session)

    feed2 = Feed(
        href="https://example.com/feed2.xml",
        title="源 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(feed2)
    await session.commit()

    sub1 = Subscription(user_id=user.id, feed_id=feed.id)
    sub2 = Subscription(user_id=user.id, feed_id=feed2.id, title="自定义")
    session.add_all([sub1, sub2])
    await session.commit()

    result = await repo.get_batch(user.id, [feed.id, feed2.id])
    assert result == {feed.id: sub1, feed2.id: sub2}

    assert await repo.get_batch(user.id, []) == {}


async def test_list_by_user(session, user, feed):
    repo = SubscriptionRepository(session)

    user2 = User(username="test2", password_hash="hash")
    session.add(user2)
    await session.commit()

    sub1 = Subscription(user_id=user.id, feed_id=feed.id)
    sub2 = Subscription(user_id=user2.id, feed_id=feed.id)
    session.add_all([sub1, sub2])
    await session.commit()

    result = await repo.list_by_user(user.id)
    assert set(result) == {sub1}


async def test_upsert(session, user, feed):
    repo = SubscriptionRepository(session)

    # 插入
    sub = await repo.upsert(user.id, feed.id, title="我的订阅")
    assert sub.user_id == user.id
    assert sub.feed_id == feed.id
    assert sub.title == "我的订阅"

    # 更新
    sub2 = await repo.upsert(user.id, feed.id, title="改名后的订阅")
    assert sub2.title == "改名后的订阅"


async def test_delete(session, user, feed):
    repo = SubscriptionRepository(session)

    sub = Subscription(user_id=user.id, feed_id=feed.id)
    session.add(sub)
    await session.commit()

    await repo.delete(sub)

    assert await repo.get(user.id, feed.id) is None


async def test_delete_by_user(session, user, feed):
    repo = SubscriptionRepository(session)

    feed2 = Feed(
        href="https://example.com/feed2.xml",
        title="源 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(feed2)
    await session.commit()

    sub1 = Subscription(user_id=user.id, feed_id=feed.id)
    sub2 = Subscription(user_id=user.id, feed_id=feed2.id)
    session.add_all([sub1, sub2])
    await session.commit()

    await repo.delete_by_user(user.id)

    assert await repo.list_by_user(user.id) == []


async def test_list_by_folder(session, user, feed):
    """list_by_folder 只返回指定文件夹的订阅，None 返回空"""
    repo = SubscriptionRepository(session)

    folder = Label(user_id=user.id, name="科技", type=LabelType.FOLDER)
    session.add(folder)
    await session.commit()

    feed2 = Feed(
        href="https://example.com/feed2.xml",
        title="源 2",
        fetched=datetime(1970, 1, 1, tzinfo=UTC),
    )
    session.add(feed2)
    await session.commit()

    sub1 = Subscription(user_id=user.id, feed_id=feed.id, folder_id=folder.id)
    sub2 = Subscription(user_id=user.id, feed_id=feed2.id)  # 无文件夹
    session.add_all([sub1, sub2])
    await session.commit()

    result = await repo.list_by_folder(folder.id)
    assert set(result) == {sub1}

    # None 应返回 []
    assert await repo.list_by_folder(None) == []
