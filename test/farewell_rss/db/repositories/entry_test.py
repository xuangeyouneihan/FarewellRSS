from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import (
    Base,
    Enclosure,
    Entry,
    Feed,
    ReadState,
    StarState,
    User,
)
from farewell_rss.db.repositories import _chunking
from farewell_rss.db.repositories.entry import EntryRepository
from farewell_rss.feed_fetcher.feed_fetcher import FetchedEnclosure, FetchedEntry


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
    # 必须 dispose：否则 aiosqlite 的连接工作线程会在测试的 event loop
    # 关闭之后才去交付结果，报 "RuntimeError: Event loop is closed"
    await engine.dispose()


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


async def test_get_batch(session, feed, monkeypatch):
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

    # 批次小到 1 时每条都是独立的一批，结果必须和上面一模一样
    # （分批最容易出的错是后一批把前一批的结果覆盖掉）
    monkeypatch.setattr(_chunking, "MAX_IDS_PER_STATEMENT", 1)
    assert await repo.get_batch([entry1.id, entry2.id]) == result


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


async def test_upsert_by_feed_deduplicates_within_batch(session, feed):
    """同一次抓取里出现重复 guid 时不能插两条

    有些源就是会重复列同一条目。按 guid 预取 existing 的做法是「快照」，如果插入后不
    把新对象放回 map，第二条同 guid 会被当成新条目 → 直接撞 uq_entries_feed_guid。
    """
    repo = EntryRepository(session)
    fetched = [
        FetchedEntry(guid="dup-guid", title="第一次"),
        FetchedEntry(guid="dup-guid", title="第二次"),
    ]

    await repo.upsert_by_feed(feed.id, fetched)

    rows = [
        entry for entry in await repo.list_by_feed(feed.id) if entry.guid == "dup-guid"
    ]
    assert len(rows) == 1
    assert rows[0].title == "第二次"  # 后一条覆盖前一条，和逐条查的旧行为一致


async def test_upsert_by_feed_without_per_entry_queries(session, feed):
    """批量 upsert 里不能有「每条一次 SELECT」的 N+1

    每轮只该有 2 条 SELECT：① 按 guid 预取已存在的条目；② 按 entry_id 预取旧附件。
    逐条查的写法会是 1 + N（条目）+ N（附件）条。
    """
    executed: list[str] = []

    # 监听要挂在本测试第一次用 session 之前
    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        executed.append(statement)

    repo = EntryRepository(session)
    fetched = [
        FetchedEntry(
            guid=f"nq-{i}",
            title="x",
            enclosures=[FetchedEnclosure(href=f"https://example.com/{i}.mp3")],
        )
        for i in range(5)
    ]

    await repo.upsert_by_feed(feed.id, fetched)  # 第一轮：全是插入
    await repo.upsert_by_feed(feed.id, fetched)  # 第二轮：全是更新（附件有变化）

    selects = [s for s in executed if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 4, (
        f"两轮共发了 {len(selects)} 条 SELECT（每轮应 2 条：guid 预取 + 附件预取）"
    )


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
    await session.flush()
    for entry in (entry1, entry2, entry3):
        session.add(
            Enclosure(entry_id=entry.id, href=f"https://example.com/{entry.id}.mp3")
        )
    await session.commit()

    # 删除条目
    await repo.delete_batch([entry1.id, entry2.id])

    # 验证删除结果
    result = await repo.list_by_feed(feed.id)
    assert set(result) == {entry3}

    # 附件跟着走，没被删的那条不受影响
    assert await session.scalar(select(func.count()).select_from(Enclosure)) == 1
    assert await session.scalar(select(Enclosure.entry_id)) == entry3.id


async def test_delete_batch_empty(session):
    """没有条目要删是空操作（prune 的常见路径），不能报错"""
    await EntryRepository(session).delete_batch([])


async def test_delete_batch_chunks(session, feed, monkeypatch):
    """待删数量超过一批时要逐批删完，而不是只删第一批

    批次大小是常量，这里改成 2 就能花 5 条数据跑到「一批放不下」的分支。
    顺带把语句数也钉住：每批固定 2 条 DELETE（附件 + 条目），不是每条 2 条——
    逐条 delete + flush 的写法会退化成 2N 条，那正是这次要避免的东西。
    """
    monkeypatch.setattr(_chunking, "MAX_IDS_PER_STATEMENT", 2)

    # 监听要挂在本测试对 session 下手之前，否则可能漏掉已建连接上的事件
    executed: list[str] = []

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        executed.append(statement)

    repo = EntryRepository(session)

    entries = [
        Entry(
            feed_id=feed.id,
            guid=f"chunk-{i}",
            fetched=datetime(1970, 1, 1, tzinfo=UTC),
        )
        for i in range(5)
    ]
    session.add_all(entries)
    await session.flush()
    for entry in entries:
        session.add(
            Enclosure(entry_id=entry.id, href=f"https://example.com/{entry.id}.mp3")
        )
    await session.commit()

    await repo.delete_batch([entry.id for entry in entries])

    assert await repo.list_by_feed(feed.id) == []
    assert await session.scalar(select(func.count()).select_from(Enclosure)) == 0

    deletes = [s for s in executed if s.lstrip().upper().startswith("DELETE")]
    assert len(deletes) == 2 * 3, (
        f"5 条按每批 2 条应该分 3 批、每批 2 条 DELETE，实际 {len(deletes)} 条"
    )


async def test_prune_by_feed(session, feed):
    """清理一个源里「没人真读过也没人收藏」的条目

    保留条件是**跨用户**的：别人真读过 / 收藏过的条目必须留下（那是别人的阅读历史），
    拿当前用户去判就会把它们删掉。
    """
    repo = EntryRepository(session)
    ts = datetime(1970, 1, 1, tzinfo=UTC)

    user1 = User(username="prune-u1", password_hash="hash")
    user2 = User(username="prune-u2", password_hash="hash")
    session.add_all([user1, user2])
    await session.flush()

    free = Entry(feed_id=feed.id, guid="free", fetched=ts)
    starred = Entry(feed_id=feed.id, guid="starred", fetched=ts)
    read_by_other = Entry(feed_id=feed.id, guid="read-by-other", fetched=ts)
    session.add_all([free, starred, read_by_other])
    await session.flush()

    session.add(Enclosure(entry_id=free.id, href="https://example.com/free.mp3"))
    session.add(StarState(user_id=user2.id, entry_id=starred.id, timestamp=ts))
    session.add(ReadState(user_id=user2.id, entry_id=read_by_other.id, timestamp=ts))
    # 「标为已读但没真读过」：不保护条目，而且要被清掉
    session.add(ReadState(user_id=user1.id, entry_id=read_by_other.id))
    await session.commit()

    assert await repo.prune_by_feed(feed.id) == 2  # 剩下 star 和 read-by-other

    left = {entry.guid for entry in await repo.list_by_feed(feed.id)}
    assert left == {"starred", "read-by-other"}
    # 被删条目的附件跟着走
    assert await session.scalar(select(func.count()).select_from(Enclosure)) == 0
    # 无时间戳的已读状态被清掉，user2 真读过的那条留着
    assert list(await session.scalars(select(ReadState.user_id))) == [user2.id]


async def test_prune_by_feed_cleans_states_without_deleting(session, feed):
    """一条条目都不用删时，仍要清掉「标为已读但没真读过」的状态

    这一步不能搭在 delete_batch 的提交上——后者在没有条目要删时会直接返回、不提交。
    """
    repo = EntryRepository(session)
    ts = datetime(1970, 1, 1, tzinfo=UTC)

    user = User(username="prune-keep", password_hash="hash")
    session.add(user)
    await session.flush()

    kept = Entry(feed_id=feed.id, guid="kept", fetched=ts)
    session.add(kept)
    await session.flush()
    session.add(StarState(user_id=user.id, entry_id=kept.id, timestamp=ts))
    session.add(ReadState(user_id=user.id, entry_id=kept.id))
    await session.commit()

    assert await repo.prune_by_feed(feed.id) == 1
    assert await session.scalar(select(func.count()).select_from(Entry)) == 1
    assert await session.scalar(select(func.count()).select_from(ReadState)) == 0


async def test_prune_by_feed_empty(session, feed):
    """源里没有条目时返回 0——调用方据此把整个源删掉"""
    assert await EntryRepository(session).prune_by_feed(feed.id) == 0


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
