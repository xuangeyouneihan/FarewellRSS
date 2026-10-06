"""init_db 的 schema 初始化：表、普通索引、FTS5

索引测试不能只看「存不存在」——有索引但查询计划是 SCAN，等于白建。
所以这里同时断言热点查询的查询计划。
"""

import os
import subprocess

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from farewell_rss.db import db as db_module

# 必需的索引清单（声明处是 models.py，这里是规格）
# 刻意没有 entries.feed_id 的单列索引：唯一约束 (feed_id, guid) 的最左前缀
# 已经覆盖「按源查条目」，再加一个是冗余索引、只会增加写放大。
# 而 ix_entries_feed_id 是以 feed_id 开头的**复合**索引，它服务的是
# 「按源取条目、并按时间分页」这个不同的查询形态，不是冗余。
INDEX_NAMES = {
    "ix_subscriptions_feed_id",
    "ix_subscriptions_folder_id",
    "ix_read_states_entry_id",
    "ix_star_states_entry_id",
    "ix_star_states_tag_id",
    # entries 上两条分页用的表达式索引（排序键是 unixepoch(coalesce(...)) 这个表达式，
    # index=True 的列索引对它无效，详见 models.py 的注释）
    "ix_entries_page",
    "ix_entries_feed_id",
}

# 上面这些索引分布在哪些表（PRAGMA index_list 要按表查）
INDEXED_TABLES = ("subscriptions", "read_states", "star_states", "entries")

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
        # 条目：published/updated 留空，让 coalesce 落到 fetched 上，
        # 与生产里「很多源不给 published」的形态一致
        await conn.execute(
            text(
                "INSERT INTO entries (feed_id, guid, title, published, updated, fetched)"
                " VALUES (:k, :k, 't', NULL, NULL, '2026-01-01 00:00:00.000000')"
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
        for table in INDEXED_TABLES:
            rows = await conn.execute(text(f"PRAGMA index_list('{table}')"))
            found |= {row[1] for row in rows.fetchall()}

    assert found >= INDEX_NAMES


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

    assert declared >= INDEX_NAMES


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
            for table in INDEXED_TABLES:
                rows = await conn.execute(text(f"PRAGMA index_list('{table}')"))
                found |= {row[1] for row in rows.fetchall()}
        return found

    # 模拟「老库」：把索引删掉
    async with engine.begin() as conn:
        for name in INDEX_NAMES:
            await conn.execute(text(f"DROP INDEX {name}"))
    assert not (INDEX_NAMES & await index_names()), "前提不成立：索引没删干净"

    await db_module.init_db()

    assert await index_names() >= INDEX_NAMES
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


# 分页查询：(SQL, 期望命中的索引名)
PAGINATION_QUERIES = {
    # 注意这**不是**端点形态：真实全局流是 entries JOIN subscriptions，见下面
    # test_reading_list_stream_merges_via_feed_index。这条只负责证明
    # 「排序键表达式和 ix_entries_page 的索引表达式逐字一致」——这件事在 join 形态的
    # 计划里看不出来（join 形态无论如何都有 TEMP B-TREE），所以必须单独留一条。
    "索引表达式与排序键一致（无过滤无 join）": (
        "SELECT * FROM entries ORDER BY"
        " unixepoch(coalesce(published, updated, fetched)) DESC, id DESC LIMIT 21",
        "ix_entries_page",
    ),
    "单源流 feed/{id}": (
        "SELECT * FROM entries WHERE feed_id = 1 ORDER BY"
        " unixepoch(coalesce(published, updated, fetched)) DESC, id DESC LIMIT 21",
        "ix_entries_feed_id",
    ),
}


async def test_pagination_queries_avoid_temp_sort(initialized_engine):
    """分页查询必须走表达式索引，且不能出现临时 B 树

    排序键是 unixepoch(coalesce(published, updated, fetched)) 这个**表达式**
    （秒级；微秒只留给 crawlTimeMsec / timestampUsec 那类展示字段），
    index=True 那种列索引对它无效（索引的是列，排序的是表达式），
    所以专门建了两条表达式索引。索引表达式必须和 repository 的排序键**逐字一致**，
    差一个函数（比如还是裸 coalesce）都会退化成临时排序。

    「命中了索引」还不够，关键是**没有 USE TEMP B-TREE**：
    有索引却仍要临时排序，等于白建——而且临时排序的代价随匹配行数增长，
    在浅分页的小数据集上看不出来，库一大就暴露。

    注意上面那条无过滤的查询是 SCAN ... USING INDEX：这不是全表扫，而是按索引
    顺序扫描、凑满 LIMIT 后提前终止；没有过滤条件时这正是最优形态。这也是它不能
    和上面的 HOT_QUERIES 共用断言（那里禁止 SCAN）的原因。

    真实端点形态（entries JOIN subscriptions）单独由
    test_reading_list_stream_merges_via_feed_index 覆盖。

    另一个坑：断言必须在 schema 定稿之后做。实测在同一连接里改过 schema
    （比如 DROP INDEX）之后紧接着 EXPLAIN QUERY PLAN，可能拿到**陈旧的计划**
    ——所以别写成「先删索引再断言计划变差」，那种反证会假通过。
    """
    async with initialized_engine.connect() as conn:
        for label, (sql, index_name) in PAGINATION_QUERIES.items():
            plan = await _plan(conn, sql)
            assert any(index_name in p for p in plan), (
                f"{label} 没用上 {index_name}: {plan}"
            )
            assert not any("TEMP B-TREE" in p for p in plan), (
                f"{label} 仍在做临时排序（索引白建）: {plan}"
            )


# 真实端点形态：api/stream.py 的全局流 → EntryRepository.list_reading_list
# （entries JOIN subscriptions、按 user_id 过滤订阅、按同一个表达式排序、LIMIT n+1）
READING_LIST_SQL = (
    "SELECT entries.* FROM entries"
    " JOIN subscriptions ON entries.feed_id = subscriptions.feed_id"
    " WHERE subscriptions.user_id = 1"
    " ORDER BY unixepoch(coalesce(entries.published, entries.updated, entries.fetched))"
    " DESC, entries.id DESC LIMIT 21"
)


async def test_reading_list_stream_merges_via_feed_index(initialized_engine):
    """全局流（真实端点形态）从 subscriptions 驱动，entries 逐源走 ix_entries_feed_id

    这个形态以前**没有被覆盖**：老的断言用的是去掉 join 的简化 SQL，只证明了
    「索引表达式和排序键逐字一致」，没证明端点自己的计划长什么样——而两者完全
    不同（简化形态 SCAN ix_entries_page 且无临时树；真实形态逐源取条目、有临时树）。

    期望的计划：
        SEARCH subscriptions USING COVERING INDEX sqlite_autoindex_subscriptions_1 (user_id=?)
        SEARCH entries USING INDEX ix_entries_feed_id (feed_id=?)
        USE TEMP B-TREE FOR ORDER BY

    最后那个 TEMP B-TREE 是**预期成本，不是退化**，别去「修」它：
    - 它是**有界 top-N 排序器**（bytecode 里 OpenEphemeral 的容量就是 LIMIT），
      只装 n 行，不是把命中条目全排一遍；
    - 外层逐源循环，内层在 ix_entries_feed_id（feed_id 开头、第三四列正是排序用的
      表达式和 id）上 SeekLE 到该源最新一条再 Prev 往回走，所以每个源内部天然有序；
    - 排序器填满后，一旦某行比排序器尾部还差，SQLite 直接跳到下一个源
      （bytecode：Last + IdxLE → Next subscriptions），该源剩下的条目一条都不读。

    代价因此是 O(订阅数 × 每源命中深度 + n log n)，不是 O(命中条目数)：10 万条
    entries、250 个订阅、命中 5 万条（50% 密度）时，LIMIT 20 实测 0.12ms。

    反过来，强制走 ix_entries_page 有序扫（feed_id IN (SELECT ...) INDEXED BY
    ix_entries_page）在同一个库上实测 292ms（订阅 5 源）/ 62ms（25 源）——因为
    ix_entries_page 里没有 feed_id，每扫一行都要回表取 feed_id 再判成员，代价跟
    entries 总量挂钩，而不是跟用户的订阅规模挂钩。下面这条「没有 SCAN」断言就是
    用来挡掉那种改写的。
    """
    async with initialized_engine.connect() as conn:
        plan = await _plan(conn, READING_LIST_SQL)
        assert any("ix_entries_feed_id" in p for p in plan), (
            f"全局流没用上 ix_entries_feed_id: {plan}"
        )
        assert not any(p.startswith("SCAN") for p in plan), (
            f"全局流出现了全表扫（多半是改成了 feed_id IN (...) + ix_entries_page）: {plan}"
        )


# ─── 数据目录的权限（2026-10-06）────────────────────────────────────


def test_restrict_data_permissions_tightens_dir_and_files(tmp_path, monkeypatch):
    """POSIX：目录收 0700，库 / WAL / .env 收 0600

    数据目录里有明文凭据（`feeds.href` 可能带 `user:pass@`）与签名密钥（`.env`），
    默认 umask 下它们是 `0644`/`0755` —— 同机其他账号（或挂了同一个卷的另一台设备）
    可以直接读。
    """
    monkeypatch.setattr(db_module.os, "name", "posix")
    monkeypatch.setattr(db_module, "DATA_DIR", str(tmp_path))
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        db_module.os, "chmod", lambda path, mode: calls.append((path, mode))
    )
    for name in ("farewell_rss.db", "farewell_rss.db-wal", ".env"):
        (tmp_path / name).write_text("x")

    db_module.restrict_data_permissions()

    # 不存在的 -shm 不碰；多收一个少收一个都会让这里不相等
    assert dict(calls) == {
        str(tmp_path): 0o700,
        str(tmp_path / "farewell_rss.db"): 0o600,
        str(tmp_path / "farewell_rss.db-wal"): 0o600,
        str(tmp_path / ".env"): 0o600,
    }


def test_restrict_data_permissions_uses_icacls_on_windows(tmp_path, monkeypatch):
    """Windows 上走 icacls（`os.chmod` 只能改只读位，碰不到 ACL）

    命令形状就是「丢掉继承 + 只授给当前账号 / SYSTEM / Administrators」，内置账号用
    SID 写（名字是本地化的，中文 Windows 上对不上）。
    """
    monkeypatch.setattr(db_module.os, "name", "nt")
    # 非 Windows 主机上没这个常量，补一个让这条分支可测
    monkeypatch.setattr(
        db_module.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
    )
    monkeypatch.setattr(db_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        db_module.os, "chmod", lambda *args: pytest.fail("Windows 上不该调 chmod")
    )
    monkeypatch.setitem(db_module.os.environ, "USERDOMAIN", "MACHINE")
    monkeypatch.setitem(db_module.os.environ, "USERNAME", "alice")
    calls: list[list[str]] = []

    def _run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    monkeypatch.setattr(db_module.subprocess, "run", _run)
    (tmp_path / "farewell_rss.db").write_text("x")

    db_module.restrict_data_permissions()

    dir_command, *file_commands = calls
    assert dir_command == [
        "icacls",
        str(tmp_path),
        "/inheritance:r",
        "/grant:r",
        "*S-1-5-18:(OI)(CI)F",
        "*S-1-5-32-544:(OI)(CI)F",
        "MACHINE\\alice:(OI)(CI)F",
    ]
    # 现有文件不会被目录的 (OI)(CI) 追溯，得单独收（只有 -shm 不存在）
    assert file_commands == [
        [
            "icacls",
            str(tmp_path / "farewell_rss.db"),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:F",
            "*S-1-5-32-544:F",
            "MACHINE\\alice:F",
        ]
    ]


def test_restrict_data_permissions_survives_icacls_failure(
    tmp_path, monkeypatch, caplog
):
    """icacls 失败（FAT/exFAT 卷、权限不足）只记 warning，不能让启动挂掉"""
    monkeypatch.setattr(db_module.os, "name", "nt")
    monkeypatch.setattr(db_module.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(db_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        db_module.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 5, stderr=b"Access is denied."
        ),
    )

    db_module.restrict_data_permissions()

    assert any("无法收紧" in record.getMessage() for record in caplog.records)


@pytest.mark.skipif(os.name != "nt", reason="icacls 只在 Windows 上")
def test_restrict_data_permissions_icacls_actually_works(tmp_path, monkeypatch, caplog):
    """真跑一次 icacls：命令与参数在真机上确实能成功

    失败路径只在记 warning（上面那条测过了），所以这里断言「一条 warning 都没有」。
    不实际验「其他账号读不到」——那需要一个真实的第二个账号。
    """
    monkeypatch.setattr(db_module, "DATA_DIR", str(tmp_path))
    (tmp_path / "farewell_rss.db").write_text("x")

    db_module.restrict_data_permissions()

    assert not [r for r in caplog.records if "无法收紧" in r.getMessage()]


def test_restrict_data_permissions_survives_chmod_failure(
    tmp_path, monkeypatch, caplog
):
    """收权限失败（只读挂载、NFS）只记一条 warning，不能让启动挂掉"""
    monkeypatch.setattr(db_module.os, "name", "posix")
    monkeypatch.setattr(db_module, "DATA_DIR", str(tmp_path))
    (tmp_path / "farewell_rss.db").write_text("x")

    def _raise(path, mode):
        raise OSError("只读文件系统")

    monkeypatch.setattr(db_module.os, "chmod", _raise)

    db_module.restrict_data_permissions()

    assert any("无法收紧" in record.getMessage() for record in caplog.records)
