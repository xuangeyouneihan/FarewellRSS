"""所有「批量 IN (...)」方法都必须把大列表拆开。

用**真常量**（不 monkeypatch）喂比一批更多的 id：SQL 是按 id 列表拼出来的，跟库里
有没有对应行无关，所以不用造那么多数据。断言「每多一批就多一条 SELECT」——如果哪个
方法漏了分批，这里会看到 1 条而不是 3 条。

（另一半保护在各自的仓库测试里：把批次改成 1 后重跑同样的断言，用来抓「后一批把前
一批结果覆盖掉」这种错法。）
"""

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from farewell_rss.db.models import Base
from farewell_rss.db.repositories import _chunking
from farewell_rss.db.repositories.entry import EntryRepository
from farewell_rss.db.repositories.feed import FeedRepository
from farewell_rss.db.repositories.label import LabelRepository
from farewell_rss.db.repositories.read_state import ReadStateRepository
from farewell_rss.db.repositories.star_state import StarStateRepository
from farewell_rss.db.repositories.subscription import SubscriptionRepository

BATCH = _chunking.MAX_IDS_PER_STATEMENT


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    # 必须 dispose：否则 aiosqlite 的连接工作线程会在测试的 event loop
    # 关闭之后才去交付结果，报 "RuntimeError: Event loop is closed"
    await engine.dispose()


async def test_batch_methods_split_large_id_lists(session):
    executed: list[str] = []

    # 监听要挂在任何语句执行之前，否则可能漏掉已建连接上的事件
    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        executed.append(statement)

    # 两批满 + 1 条 → 应该发 3 条 SELECT
    ids = [10**9 + i for i in range(BATCH * 2 + 1)]

    cases = {
        "entry.get_batch": lambda: EntryRepository(session).get_batch(ids),
        "feed.get_batch": lambda: FeedRepository(session).get_batch(ids),
        "label.get_batch": lambda: LabelRepository(session).get_batch(ids),
        "subscription.get_batch": lambda: SubscriptionRepository(session).get_batch(
            1, ids
        ),
        "subscription.subscription_count_batch": lambda: SubscriptionRepository(
            session
        ).subscription_count_batch(ids),
        "read_state.get_batch": lambda: ReadStateRepository(session).get_batch(1, ids),
        "star_state.get_batch": lambda: StarStateRepository(session).get_batch(1, ids),
        "read_state.read_count_batch": lambda: ReadStateRepository(
            session
        ).read_count_batch(ids),
        "star_state.star_count_batch": lambda: StarStateRepository(
            session
        ).star_count_batch(ids),
    }

    for label, call in cases.items():
        executed.clear()
        # 这些 id 在库里都不存在，所以结果必然是空的
        assert await call() == {}
        selects = [s for s in executed if s.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 3, (
            f"{label} 发了 {len(selects)} 条 SELECT（{len(ids)} 个 id 按每批 {BATCH}"
            f" 应该拆成 3 批）—— 多半是漏了 chunked()"
        )
