"""init_db 的 schema 初始化：表、普通索引、FTS5

索引测试不能只看「存不存在」——有索引但查询计划是 SCAN，等于白建。
所以这里同时断言热点查询的查询计划。
"""

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from farewell_rss.db import db as db_module

# 必需的索引清单（声明处是 models.py，这里是规格）
# 刻意不含 entries.feed_id：唯一约束 (feed_id, guid) 的最左前缀已覆盖它，
# 再建一个是冗余索引，只会增加写放大。
INDEX_NAMES = {
    "ix_subscriptions_feed_id",
    "ix_subscriptions_folder_id",
    "ix_read_states_entry_id",
    "ix_star_states_entry_id",
    "ix_star_states_tag_id",
}

# 服务端每次请求或每轮调度都会跑的热点查询
HOT_QUERIES = {
    "孤儿源清理查订阅数": "SELECT count(*) FROM subscriptions WHERE feed_id = 1",
    "按文件夹列订阅": "SELECT * FROM subscriptions WHERE folder_id = 1",
    "清理条目的无时间戳已读": (
        "DELETE FROM read_states WHERE entry_id = 1 AND timestamp IS NULL"
    ),
    "已读计数": (
        "SELECT entry_id, count(*) FROM read_states"
        " WHERE entry_id IN (1, 2) AND timestamp IS NOT NULL GROUP BY entry_id"
    ),
    "按标签列收藏": "SELECT * FROM star_states WHERE tag_id = 1",
}


@pytest_asyncio.fixture
async def initialized_engine(monkeypatch, tmp_path):
    """把模块级 engine 指到临时库，再跑真正的 init_db"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db_module, "engine", engine)
    await db_module.init_db()

    # 塞点高选择度的数据（每个值只命中一行，和生产里「按 feed_id 查」的形态一致）。
    # 刻意不跑 ANALYZE —— 要测的就是生产环境没有统计信息时的真实计划。
    rows = [{"k": i + 1} for i in range(2000)]
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO subscriptions (user_id, feed_id, folder_id)"
                " VALUES (:k, :k, :k)"
            ),
            rows,
        )
        await conn.execute(
            text(
                "INSERT INTO read_states (user_id, entry_id, timestamp)"
                " VALUES (:k, :k, NULL)"
            ),
            rows,
        )
        await conn.execute(
            text(
                "INSERT INTO star_states (user_id, entry_id, tag_id, timestamp)"
                " VALUES (:k, :k, :k, '2026-01-01 00:00:00.000000')"
            ),
            rows,
        )
    yield engine
    await engine.dispose()


async def _plan(conn, sql: str) -> list[str]:
    rows = await conn.execute(text("EXPLAIN QUERY PLAN " + sql))
    return [row[-1] for row in rows.fetchall()]


async def test_init_db_creates_indexes(initialized_engine):
    async with initialized_engine.connect() as conn:
        found: set[str] = set()
        for table in ("subscriptions", "read_states", "star_states"):
            rows = await conn.execute(text(f"PRAGMA index_list('{table}')"))
            found |= {row[1] for row in rows.fetchall()}

    assert INDEX_NAMES <= found


async def test_init_db_is_idempotent(initialized_engine):
    """老库每次启动都会重跑 init_db，必须能重复执行"""
    await db_module.init_db()
    await db_module.init_db()


def test_models_declare_expected_indexes():
    """索引声明在模型里，这个测试把「哪些索引是必需的」钉成规格"""
    from farewell_rss.db.models import Base

    declared = {
        index.name for table in Base.metadata.tables.values() for index in table.indexes
    }

    assert INDEX_NAMES <= declared


async def test_init_db_restores_missing_indexes(monkeypatch, tmp_path):
    """老库（表在、索引缺）跑一次 init_db 就能补上

    create_all 对已存在的表整张跳过，所以这件事只能由 init_db 单独负责。
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    monkeypatch.setattr(db_module, "engine", engine)
    await db_module.init_db()

    async def index_names() -> set[str]:
        found: set[str] = set()
        async with engine.connect() as conn:
            for table in ("subscriptions", "read_states", "star_states"):
                rows = await conn.execute(text(f"PRAGMA index_list('{table}')"))
                found |= {row[1] for row in rows.fetchall()}
        return found

    # 模拟「老库」：把索引删掉
    async with engine.begin() as conn:
        for name in INDEX_NAMES:
            await conn.execute(text(f"DROP INDEX {name}"))
    assert not (INDEX_NAMES & await index_names()), "前提不成立：索引没删干净"

    await db_module.init_db()

    assert INDEX_NAMES <= await index_names()
    await engine.dispose()


async def test_hot_queries_use_index_not_full_scan(initialized_engine):
    """热点查询出现 SCAN 就说明索引白建了"""
    async with initialized_engine.connect() as conn:
        for label, sql in HOT_QUERIES.items():
            plan = await _plan(conn, sql)
            assert not any(p.startswith("SCAN") for p in plan), (
                f"{label} 仍在全表扫: {plan}"
            )
            assert any("SEARCH" in p for p in plan), f"{label} 没用上索引: {plan}"
