import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base, Label, LabelType, User
from farewell_rss.db.repositories.label import LabelRepository


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


async def test_get(session, user):
    repo = LabelRepository(session)
    label = Label(user_id=user.id, name="文件夹", type=LabelType.FOLDER)
    session.add(label)
    await session.commit()

    result = await repo.get(label.id)
    assert result == label


async def test_get_batch(session, user):
    repo = LabelRepository(session)

    l1 = Label(user_id=user.id, name="A", type=LabelType.FOLDER)
    l2 = Label(user_id=user.id, name="B", type=LabelType.TAG)
    session.add_all([l1, l2])
    await session.commit()

    result = await repo.get_batch([l1.id, l2.id])
    assert result == {l1.id: l1, l2.id: l2}

    assert await repo.get_batch([]) == {}


async def test_get_by_user_name_type(session, user):
    repo = LabelRepository(session)

    label = Label(user_id=user.id, name="科技", type=LabelType.FOLDER)
    session.add(label)
    await session.commit()

    result = await repo.get_by_user_name_type(user.id, "科技", LabelType.FOLDER)
    assert result == label


async def test_list_by_user(session, user):
    repo = LabelRepository(session)

    l1 = Label(user_id=user.id, name="A", type=LabelType.FOLDER)
    l2 = Label(user_id=user.id, name="B", type=LabelType.TAG)
    session.add_all([l1, l2])
    await session.commit()

    result = await repo.list_by_user(user.id)
    assert set(result) == {l1, l2}


async def test_create(session, user):
    repo = LabelRepository(session)

    label = await repo.create(user.id, "新文件夹", LabelType.FOLDER)
    assert label.id is not None
    assert label.name == "新文件夹"
    assert label.type == LabelType.FOLDER


async def test_update(session, user):
    repo = LabelRepository(session)

    label = Label(user_id=user.id, name="旧名", type=LabelType.FOLDER)
    session.add(label)
    await session.commit()

    updated = await repo.update(label, "新名")
    assert updated is not None
    assert updated.name == "新名"


async def test_update_conflict(session, user):
    """更新为已存在的同名同类型应返回 None"""
    repo = LabelRepository(session)

    l1 = Label(user_id=user.id, name="A", type=LabelType.FOLDER)
    l2 = Label(user_id=user.id, name="B", type=LabelType.FOLDER)
    session.add_all([l1, l2])
    await session.commit()

    result = await repo.update(l1, "B")
    assert result is None


async def test_delete(session, user):
    repo = LabelRepository(session)

    label = Label(user_id=user.id, name="待删除", type=LabelType.FOLDER)
    session.add(label)
    await session.commit()

    await repo.delete(label)

    assert await repo.get(label.id) is None


async def test_delete_by_user(session, user):
    repo = LabelRepository(session)

    l1 = Label(user_id=user.id, name="A", type=LabelType.FOLDER)
    l2 = Label(user_id=user.id, name="B", type=LabelType.TAG)
    session.add_all([l1, l2])
    await session.commit()

    await repo.delete_by_user(user.id)

    assert await repo.list_by_user(user.id) == []
