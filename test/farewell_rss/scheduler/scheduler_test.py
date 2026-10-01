"""调度器测试：并发更新的 session 隔离 + 孤儿源清理"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base, Entry, Feed, ReadState, Subscription, User
from farewell_rss.factory import build_services
from farewell_rss.feed_fetcher.feed_fetcher import FetchedEntry, FetchedFeed
from farewell_rss.scheduler import scheduler
from farewell_rss.scheduler.scheduler import prune_orphan_feeds
from farewell_rss.services import feed as feed_service_module

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


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
    # 必须 dispose：否则 aiosqlite 的连接工作线程会在测试的 event loop
    # 关闭之后才去交付结果，报 "RuntimeError: Event loop is closed"
    await engine.dispose()


async def _add_feed(session, href: str) -> Feed:
    feed = Feed(href=href, title=href, fetched=_EPOCH)
    session.add(feed)
    await session.commit()
    return feed


async def _add_entry(session, feed: Feed, guid: str) -> Entry:
    entry = Entry(feed_id=feed.id, guid=guid, title=guid, fetched=_EPOCH)
    session.add(entry)
    await session.commit()
    return entry


async def test_prune_orphan_feed_without_references(session):
    """没人订阅、也没人读过/收藏的源：条目连同源一起删掉"""
    feed = await _add_feed(session, "https://example.com/orphan.xml")
    await _add_entry(session, feed, "g1")

    deleted = await prune_orphan_feeds(build_services(session))

    assert deleted == [feed.id]
    assert await session.scalar(select(Feed).where(Feed.id == feed.id)) is None
    assert list((await session.scalars(select(Entry))).all()) == []


async def test_prune_keeps_entries_with_read_history(session):
    """孤儿源里被真实阅读历史引用的条目要保留，源随之保留（历史还能看）"""
    user = User(username="u", password_hash="h")
    session.add(user)
    await session.commit()
    feed = await _add_feed(session, "https://example.com/history.xml")
    read_entry = await _add_entry(session, feed, "g1")
    await _add_entry(session, feed, "g2")
    session.add(
        ReadState(
            user_id=user.id,
            entry_id=read_entry.id,
            timestamp=datetime(1970, 1, 2, tzinfo=UTC),
        )
    )
    await session.commit()

    deleted = await prune_orphan_feeds(build_services(session))

    assert deleted == []
    assert [e.id for e in (await session.scalars(select(Entry))).all()] == [
        read_entry.id
    ]
    assert await session.scalar(select(Feed).where(Feed.id == feed.id)) is not None
    assert (
        await session.scalar(
            select(ReadState).where(ReadState.entry_id == read_entry.id)
        )
        is not None
    )


async def test_prune_skips_feed_with_subscribers(session):
    """还有订阅者的源完全不动"""
    user = User(username="u", password_hash="h")
    session.add(user)
    await session.commit()
    feed = await _add_feed(session, "https://example.com/subscribed.xml")
    await _add_entry(session, feed, "g1")
    session.add(Subscription(user_id=user.id, feed_id=feed.id))
    await session.commit()

    assert await prune_orphan_feeds(build_services(session)) == []
    assert await session.scalar(select(Entry)) is not None
    assert await session.scalar(select(Feed).where(Feed.id == feed.id)) is not None


async def test_prune_is_idempotent(session):
    """重复清理同一批孤儿源不报错、不重复上报"""
    feed = await _add_feed(session, "https://example.com/orphan.xml")
    await _add_entry(session, feed, "g1")

    assert await prune_orphan_feeds(build_services(session)) == [feed.id]
    assert await prune_orphan_feeds(build_services(session)) == []


async def test_prune_survives_one_bad_feed(session, monkeypatch):
    """单个源清理出错不能带走整轮清理

    run() 的 while True 没有兜底：异常跑出去这个调度任务就再也不会醒来了，
    所以 per-feed 的异常必须在这里吞掉并记日志（对齐 _update_one 的做法）。
    """
    bad = await _add_feed(session, "https://example.com/bad.xml")
    good = await _add_feed(session, "https://example.com/good.xml")
    await _add_entry(session, bad, "g1")
    await _add_entry(session, good, "g2")
    await session.commit()

    services = build_services(session)
    original_prune = services.feed.prune

    async def flaky_prune(feed):
        if feed.id == bad.id:
            raise RuntimeError("boom")
        return await original_prune(feed)

    monkeypatch.setattr(services.feed, "prune", flaky_prune)

    # 坏源排在前面（先建的），它抛异常后好的那个仍然要被清掉
    assert await prune_orphan_feeds(services) == [good.id]
    assert await session.scalar(select(Feed).where(Feed.id == good.id)) is None
    assert await session.scalar(select(Feed).where(Feed.id == bad.id)) is not None


# ─── 并发更新：每个任务一个 session（2026-10-01）────────────────────────

_FEEDS = 12  # 源数要大于并发上限，才能造出「同时在抓」的局面
_CONCURRENCY = 3  # 正测试用的并发上限（小于源数就够）
_FETCHED = datetime(2024, 1, 1, tzinfo=UTC)  # 假抓取返回的时刻 = 「刷新过」的标记


@pytest_asyncio.fixture
async def file_session_factory(tmp_path):
    """文件库 + session 工厂

    并发这块必须用文件库：内存库走 StaticPool（所有 session 共用同一条连接），而这里
    要验证的恰恰是「每个任务各自拿一条连接」；文件库才是生产的形态（QueuePool）。
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'concurrency.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _fake_fetch(delay: float = 0.01):
    """假抓取：先睡一下（让并发真的重叠），再返回一个带一条条目的源"""

    async def _fetch(url, etag=None, modified=None):
        await asyncio.sleep(delay)
        return FetchedFeed(
            href=url,
            title=url,
            fetched=_FETCHED,
            entries=[FetchedEntry(guid=f"{url}#1", title="新条目")],
        )

    return _fetch


async def _seed_subscribed_feeds(session_factory, count: int) -> None:
    """一个用户 + count 个「有订阅者、且已过 TTL」的源"""
    async with session_factory() as session:
        user = User(username="u", password_hash="h")
        session.add(user)
        await session.flush()
        for i in range(count):
            feed = Feed(
                href=f"https://example.com/{i}.xml", title=f"f{i}", fetched=_EPOCH
            )
            session.add(feed)
            await session.flush()
            session.add(Subscription(user_id=user.id, feed_id=feed.id))
        await session.commit()


async def _written(session_factory) -> tuple[int, int]:
    """(写进库的条目数, fetched 被刷新过的源数)"""
    async with session_factory() as session:
        entries = await session.scalar(select(func.count()).select_from(Entry)) or 0
        feeds = list((await session.scalars(select(Feed))).all())
    return entries, sum(1 for feed in feeds if feed.fetched == _FETCHED)


async def test_update_all_feeds_writes_every_feed(
    file_session_factory, monkeypatch, caplog
):
    """并发更新时，每个源都要真的写进库

    每个任务各开各的 session：旧写法（run() 只建一个 session 传进来）实测 12 个源
    会有 11 条 `Session is already flushing`、条目只写进 10/12、时间戳只更新 1/12，
    而且全被 per-feed 的 except 吞成日志（静默降级：时间戳没更新 → 每轮重复抓）。
    """
    await _seed_subscribed_feeds(file_session_factory, _FEEDS)
    monkeypatch.setattr(scheduler, "SessionLocal", file_session_factory)
    monkeypatch.setattr(scheduler, "_MAX_CONCURRENCY", _CONCURRENCY)
    monkeypatch.setattr(feed_service_module, "fetch", _fake_fetch())
    caplog.set_level(logging.ERROR, logger="farewell_rss.scheduler.scheduler")

    await scheduler._update_all_feeds()

    entries, refreshed = await _written(file_session_factory)
    assert entries == _FEEDS, "有条目没写进库"
    assert refreshed == _FEEDS, "有源的 fetched 没被刷新（下一轮会重复抓）"
    assert [r.getMessage() for r in caplog.records] == []


async def test_failed_fetch_leaves_no_half_state(
    file_session_factory, monkeypatch, caplog
):
    """抓取中途失败：源元数据不能单独落库（否则 TTL 内不再重试，条目永久丢失）

    repository 从「自行 commit」改成「只 flush」之后，一次抓取的源元数据 + 条目 +
    附件才落在同一个事务里。这里让**条目阶段**失败（naive datetime：模型的
    UTCDateTime 只接受 UTC，是真实的写入失败路径），而此时源的 fetched 已经因为
    autoflush 落到了库上——事务边界一旦破了，它就会留下来，于是这个源在 TTL 内
    被一直跳过，用户看到的是「源在那儿、但再也不更新」。

    对照：上面 test_update_all_feeds_writes_every_feed 是「顺利抓取要写进去」。
    """
    await _seed_subscribed_feeds(file_session_factory, 1)
    monkeypatch.setattr(scheduler, "SessionLocal", file_session_factory)

    async def _bad_fetch(url, etag=None, modified=None):
        return FetchedFeed(
            href=url,
            title=url,
            fetched=_FETCHED,
            entries=[
                FetchedEntry(
                    guid="bad-entry",
                    title="坏条目",
                    published=datetime(2024, 1, 1),  # 没有 tzinfo → 写库时抛错
                )
            ],
        )

    monkeypatch.setattr(feed_service_module, "fetch", _bad_fetch)
    caplog.set_level(logging.ERROR, logger="farewell_rss.scheduler.scheduler")

    await scheduler._update_all_feeds()

    entries, refreshed = await _written(file_session_factory)
    assert entries == 0, "条目阶段失败，却有条目落了库（半个事务）"
    assert refreshed == 0, "源被标成『抓过了』，TTL 内不会再重试 → 条目永久丢失"
    # 失败必须留下痕迹，不能静默
    assert any("更新订阅源" in r.getMessage() for r in caplog.records)


@pytest.mark.filterwarnings("ignore::sqlalchemy.exc.SAWarning")
async def test_shared_session_does_lose_writes(
    file_session_factory, monkeypatch, caplog
):
    """否定对照：故意让所有任务共用一个 session（也就是修之前的写法），必须看出丢更新

    这条不是测修好的代码，而是证明上面那条断言有区分度 —— 否则它可能永远绿。
    并发上限拉满（12 个源同时起）照抄当时实测出问题的那组参数。
    """
    await _seed_subscribed_feeds(file_session_factory, _FEEDS)
    monkeypatch.setattr(scheduler, "_MAX_CONCURRENCY", _FEEDS)
    monkeypatch.setattr(feed_service_module, "fetch", _fake_fetch())
    caplog.set_level(logging.ERROR, logger="farewell_rss.scheduler.scheduler")

    shared = file_session_factory()

    @asynccontextmanager
    async def _shared_factory():
        yield shared  # 不新建也不关：所有任务共用同一个 session

    monkeypatch.setattr(scheduler, "SessionLocal", _shared_factory)
    try:
        await scheduler._update_all_feeds()
        entries, refreshed = await _written(file_session_factory)
    finally:
        await shared.close()

    assert entries < _FEEDS or refreshed < _FEEDS, (
        "共用一个 session 竟然全都写进去了？那正测试证明不了什么"
    )


async def test_run_survives_a_bad_cycle(file_session_factory, monkeypatch, caplog):
    """单轮出错不能让调度任务退出（这个任务没人重启）

    `main.py` 用 `create_task(scheduler_run())` 起的它，异常跑出 while True 就再也
    没人叫醒，之后整个实例既不刷新也不清理。所以刷新和清理各自要包一层。
    """
    monkeypatch.setattr(scheduler, "SessionLocal", file_session_factory)
    monkeypatch.setattr(scheduler, "_REFRESH_INTERVAL", 0)
    caplog.set_level(logging.ERROR, logger="farewell_rss.scheduler.scheduler")
    cycles: list[str] = []

    async def bad_update() -> None:
        cycles.append("update")
        raise RuntimeError("boom")

    async def fake_cleanup(services) -> list[int]:
        cycles.append("cleanup")
        return []

    monkeypatch.setattr(scheduler, "_update_all_feeds", bad_update)
    monkeypatch.setattr(scheduler, "prune_orphan_feeds", fake_cleanup)

    task = asyncio.create_task(scheduler.run())
    try:
        for _ in range(1000):
            if len(cycles) >= 4:  # 至少跑满两轮
                break
            await asyncio.sleep(0)
        else:
            pytest.fail("run() 两轮都没跑到，是不是卡住了")
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    assert cycles.count("update") >= 2, "第一轮抛异常后 while True 就退出了"
    assert cycles.count("cleanup") >= 1, "刷新出错不该连累清理"
    assert any("刷新订阅源时出错" in r.getMessage() for r in caplog.records)
