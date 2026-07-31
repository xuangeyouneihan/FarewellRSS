import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base
from farewell_rss.db.repositories.user import UserRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


async def test_register(session):
    repo = UserRepository(session)

    user = await repo.register("alice", "hash123", friendly_name="Alice")
    assert user.id is not None
    assert user.username == "alice"
    assert user.friendly_name == "Alice"
    assert not user.is_admin


async def test_get(session):
    repo = UserRepository(session)

    user = await repo.register("bob", "hash456")
    result = await repo.get(user.id)
    assert result == user


async def test_get_by_username(session):
    repo = UserRepository(session)

    user = await repo.register("charlie", "hash789")
    result = await repo.get_by_username("charlie")
    assert result == user


async def test_list_(session):
    repo = UserRepository(session)

    u1 = await repo.register("u1", "h1")
    u2 = await repo.register("u2", "h2")

    result = await repo.list_()
    assert set(result) == {u1, u2}


async def test_list_admins(session):
    repo = UserRepository(session)

    await repo.register("normal", "h1")
    admin = await repo.register("admin", "h2", is_admin=True)

    result = await repo.list_admins()
    assert set(result) == {admin}


async def test_update_username(session):
    repo = UserRepository(session)

    user = await repo.register("old", "hash")
    result = await repo.update_username(user, "new")
    assert result is not None
    assert result.username == "new"


async def test_update_username_conflict(session):
    """更新为已存在的用户名应返回 None"""
    repo = UserRepository(session)

    await repo.register("alice", "h1")
    bob = await repo.register("bob", "h2")

    result = await repo.update_username(bob, "alice")
    assert result is None


async def test_update_password(session):
    repo = UserRepository(session)

    user = await repo.register("pw", "old_hash")
    result = await repo.update_password(user, "new_hash")
    assert result is not None
    assert result.password_hash == "new_hash"


async def test_update_profile(session):
    repo = UserRepository(session)

    user = await repo.register("profile", "hash")
    result = await repo.update_profile(user, friendly_name="新昵称")
    assert result is not None
    assert result.friendly_name == "新昵称"


async def test_update_admin_state(session):
    repo = UserRepository(session)

    user = await repo.register("user", "hash")
    result = await repo.update_admin_state(user, is_admin=True)
    assert result is not None
    assert result.is_admin


async def test_mark_as_deleted(session):
    repo = UserRepository(session)

    user = await repo.register("del", "hash")
    assert user.deleted_at is None

    await repo.mark_as_deleted(user)
    assert user.deleted_at is not None


async def test_delete(session):
    repo = UserRepository(session)

    user = await repo.register("bye", "hash")
    uid = user.id

    await repo.delete(user)

    assert await repo.get(uid) is None
