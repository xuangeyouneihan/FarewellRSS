"""后台作业测试

重点验证 hard_delete_user **自建 session**：它用的是进程级的 SessionLocal，
而不是某个请求作用域的 session（后者在请求结束后会被关闭）。
这里把 jobs.SessionLocal 指向临时文件库来验证整条清理链路。
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss import jobs
from farewell_rss.db.models import (
    Base,
    Entry,
    Feed,
    Label,
    LabelType,
    ReadState,
    StarState,
    Subscription,
    User,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    # 用文件库而不是 :memory:，这样多个 session 连的是同一个数据库
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS entry_fts USING fts5("
                "title, content_plain, summary_plain, "
                "tokenize='trigram', content='entries', content_rowid='id')"
            )
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(seed_session):
    """建两个用户 + 一个源 + 一条条目，两人都订阅"""
    doomed = User(username="doomed", password_hash="h")
    keeper = User(username="keeper", password_hash="h")
    seed_session.add_all([doomed, keeper])
    await seed_session.commit()

    feed = Feed(href="https://example.com/f.xml", title="源", fetched=_EPOCH)
    seed_session.add(feed)
    await seed_session.commit()

    entry = Entry(feed_id=feed.id, guid="g1", title="文章", fetched=_EPOCH)
    seed_session.add(entry)
    await seed_session.commit()

    seed_session.add_all([
        Subscription(user_id=doomed.id, feed_id=feed.id),
        Subscription(user_id=keeper.id, feed_id=feed.id),
        ReadState(
            user_id=doomed.id,
            entry_id=entry.id,
            timestamp=datetime(1970, 1, 2, tzinfo=UTC),
        ),
        StarState(
            user_id=doomed.id,
            entry_id=entry.id,
            timestamp=datetime(1970, 1, 2, tzinfo=UTC),
        ),
        Label(user_id=doomed.id, name="文件夹", type=LabelType.FOLDER),
    ])
    await seed_session.commit()
    return doomed.id, keeper.id, feed.id, entry.id


async def test_hard_delete_user_purges_only_that_user(monkeypatch, session_factory):
    """彻底删除某个用户时，清掉他的全部数据，别的用户不受影响"""
    monkeypatch.setattr(jobs, "SessionLocal", session_factory)
    async with session_factory() as seed_session:
        doomed_id, keeper_id, feed_id, entry_id = await _seed(seed_session)

    await jobs.hard_delete_user(doomed_id)

    async with session_factory() as check:
        assert await check.get(User, doomed_id) is None
        assert await check.get(User, keeper_id) is not None

        # 该用户的四类数据都清掉了，keeper 的订阅还在
        for model in (Subscription, ReadState, StarState, Label):
            remaining = list((await check.scalars(select(model))).all())
            assert all(row.user_id == keeper_id for row in remaining), model.__name__
        assert len(list((await check.scalars(select(Subscription))).all())) == 1

        # 源和条目**不**在这里删（还有 keeper 订阅着），由调度器负责回收
        assert await check.get(Feed, feed_id) is not None
        assert await check.get(Entry, entry_id) is not None


async def test_hard_delete_user_ignores_missing_user(monkeypatch, session_factory):
    """用户不存在时安静跳过，不抛异常（后台作业重复触发时不至于炸）"""
    monkeypatch.setattr(jobs, "SessionLocal", session_factory)

    await jobs.hard_delete_user(12345)
