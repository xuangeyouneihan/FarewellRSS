from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base, Enclosure, Entry, Feed
from farewell_rss.db.repositories.enclosure import EnclosureRepository
from farewell_rss.feed_fetcher.feed_fetcher import FetchedEnclosure


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
async def entry_factory(session, feed):
    class _EntryFactory:
        def __init__(self):
            self.counter = 0

        async def __call__(self) -> Entry:
            self.counter += 1
            entry = Entry(
                feed_id=feed.id,
                guid=f"test-guid-{self.counter}",
                title=f"测试文章 {self.counter}",
                link=f"https://example.com/{self.counter}",
                fetched=datetime(1970, 1, 1, tzinfo=UTC),
            )
            session.add(entry)
            await session.commit()
            return entry

    return _EntryFactory()


async def test_list_by_entry(session, entry_factory):
    repo = EnclosureRepository(session)

    entry1 = await entry_factory()
    entry2 = await entry_factory()

    # 无附件
    assert await repo.list_by_entry(entry1.id) == []
    assert await repo.list_by_entry(entry2.id) == []

    # 给 entry1 添加附件
    enclosure1 = Enclosure(entry_id=entry1.id, href="https://example.com/file1.mp3")
    enclosure2 = Enclosure(entry_id=entry1.id, href="https://example.com/file2.mp3")
    session.add_all([enclosure1, enclosure2])
    await session.commit()

    result1 = await repo.list_by_entry(entry1.id)
    assert set(result1) == {enclosure1, enclosure2}

    # entry2 不受影响
    assert await repo.list_by_entry(entry2.id) == []


async def test_update_by_entry(session, entry_factory):
    repo = EnclosureRepository(session)

    entry1 = await entry_factory()
    entry2 = await entry_factory()

    # 给 entry1 插初始附件，entry2 也插一个对照
    enclosure_control = Enclosure(
        entry_id=entry2.id, href="https://example.com/control.mp3"
    )
    session.add(enclosure_control)
    await session.commit()

    await repo.update_by_entry(
        entry1.id,
        [
            FetchedEnclosure(
                href="https://example.com/file1.mp3", length=100, type="audio/mpeg"
            ),
            FetchedEnclosure(
                href="https://example.com/file2.mp3", length=200, type="audio/mpeg"
            ),
        ],
    )

    result1 = await repo.list_by_entry(entry1.id)
    result1.sort(key=lambda e: e.length)
    assert len(result1) == 2
    assert result1[0].href == "https://example.com/file1.mp3"
    assert result1[1].href == "https://example.com/file2.mp3"

    # 对照 entry 不受影响
    result_control = await repo.list_by_entry(entry2.id)
    assert set(result_control) == {enclosure_control}

    # 更新 entry1 附件：删 file1，保留并更新 file2，加 file3
    await repo.update_by_entry(
        entry1.id,
        [
            FetchedEnclosure(
                href="https://example.com/file2.mp3", length=250, type="audio/mpeg"
            ),
            FetchedEnclosure(
                href="https://example.com/file3.mp3", length=300, type="audio/mpeg"
            ),
        ],
    )

    result2 = await repo.list_by_entry(entry1.id)
    result2.sort(key=lambda e: e.length)
    assert len(result2) == 2
    assert result2[0].href == "https://example.com/file2.mp3"
    assert result2[0].length == 250
    assert result2[1].href == "https://example.com/file3.mp3"

    # 对照 entry 仍然不受影响
    result_control2 = await repo.list_by_entry(entry2.id)
    assert set(result_control2) == {enclosure_control}


async def test_delete_by_entry(session, entry_factory):
    repo = EnclosureRepository(session)

    entry1 = await entry_factory()
    entry2 = await entry_factory()

    # 两个 entry 各加附件
    enc1 = Enclosure(entry_id=entry1.id, href="https://example.com/f1.mp3")
    enc2 = Enclosure(entry_id=entry1.id, href="https://example.com/f2.mp3")
    enc_ctrl = Enclosure(entry_id=entry2.id, href="https://example.com/ctrl.mp3")
    session.add_all([enc1, enc2, enc_ctrl])
    await session.commit()

    # 删除 entry1 的附件
    await repo.delete_by_entry(entry1.id)
    assert await repo.list_by_entry(entry1.id) == []

    # 对照 entry 的附件毫发无伤
    result_ctrl = await repo.list_by_entry(entry2.id)
    assert set(result_ctrl) == {enc_ctrl}
