import logging
import os

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, DropIndex

_logger = logging.getLogger(__name__)

DATA_DIR = os.path.expanduser(
    os.getenv("FAREWELL_RSS_DATA_DIR", "~/.local/share/farewell-rss")
)
_logger.info("数据目录: %s", DATA_DIR)

db_path = os.path.join(DATA_DIR, "farewell_rss.db")
engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")


@event.listens_for(engine.sync_engine, "connect")
def _enable_wal(dbapi_connection, connection_record):
    """启用 WAL 模式，支持一写多读并发"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """创建所有表、模型里声明的索引、以及 FTS5 搜索索引

    幂等：老库每次启动都会重跑，缺失的索引会被补上。
    """
    from .models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # create_all 只建「不存在的表」，对**已存在的表整张跳过**——所以老库拿不到
        # 后来新增的索引。这里把模型里声明的索引对齐一遍（幂等），于是索引的唯一
        # 声明处仍然只有模型，不必在别处重复写一遍 DDL。
        #
        # 用 IF NOT EXISTS 而不是 checkfirst：SQLAlchemy **不支持反射表达式索引**
        # （entries 上的 unixepoch(coalesce(published, updated, fetched)) 那两个），
        # checkfirst 会把已存在的表达式索引误判为「不存在」，于是重复执行裸 CREATE INDEX
        # 而报错。
        #
        # 但 IF NOT EXISTS 只看名字，**改了索引表达式它不会更新已存在的索引**——老库会
        # 继续用旧表达式，ORDER BY 用不上索引、静默退化成临时 B 树排序。所以这里对一句
        # DDL：对不上就先 DROP 再建。sqlite_master 里存的就是当初执行的原文，与模型这边
        # 编译出来的 DDL 同源，归一化（去 IF NOT EXISTS、压空白）后可以直接比字符串。
        def _normalize(sql: str) -> str:
            return " ".join(sql.replace("IF NOT EXISTS ", "").split())

        def _reconcile_indexes(sync_conn) -> None:
            existing = {
                row[0]: _normalize(row[1]) if row[1] else None
                for row in sync_conn.execute(
                    text("SELECT name, sql FROM sqlite_master WHERE type = 'index'")
                )
            }
            for table in Base.metadata.sorted_tables:
                for index in table.indexes:
                    if existing.get(index.name) == _normalize(str(CreateIndex(index))):
                        continue
                    if index.name in existing:
                        _logger.warning("索引 %s 定义已变化，重建", index.name)
                        sync_conn.execute(DropIndex(index))
                    sync_conn.execute(CreateIndex(index, if_not_exists=True))

        await conn.run_sync(_reconcile_indexes)

        await conn.execute(
            text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS entry_fts USING fts5(
                title,
                content_plain,
                summary_plain,
                tokenize='trigram',
                content='entries',
                content_rowid='id'
            )
        """)
        )
        await conn.execute(
            text("""
            CREATE TRIGGER IF NOT EXISTS entry_fts_ai AFTER INSERT ON entries BEGIN
                INSERT INTO entry_fts(rowid, title, content_plain, summary_plain)
                VALUES (new.id, new.title, new.content_plain, new.summary_plain);
            END
        """)
        )
        await conn.execute(
            text("""
            CREATE TRIGGER IF NOT EXISTS entry_fts_ad AFTER DELETE ON entries BEGIN
                INSERT INTO entry_fts(entry_fts, rowid, title, content_plain, summary_plain)
                VALUES ('delete', old.id, old.title, old.content_plain, old.summary_plain);
            END
        """)
        )
        await conn.execute(
            text("""
            CREATE TRIGGER IF NOT EXISTS entry_fts_au AFTER UPDATE ON entries BEGIN
                INSERT INTO entry_fts(entry_fts, rowid, title, content_plain, summary_plain)
                VALUES ('delete', old.id, old.title, old.content_plain, old.summary_plain);
                INSERT INTO entry_fts(rowid, title, content_plain, summary_plain)
                VALUES (new.id, new.title, new.content_plain, new.summary_plain);
            END
        """)
        )


async def get_session():
    """**请求作用域的事务边界**：repository 只 flush，提交与回滚都归这里。

    一个请求里跨 service 的多步写（quickadd 的 feed + subscription、批量已读的
    “删旧状态 + 插新状态”……）因此落在同一个事务里：任何一步抛异常整批回滚，
    不会留下半截状态。

    需要“先落库、再触发后台作业”的地方（见 api/auth.py 的 DeleteAccount）要在
    端点里显式 commit：后台作业自建 session，看不见未提交的数据。

    （历史上这里不能用 `session.begin()` 包裹，因为 repository 会自行 commit、
    提前关掉显式事务；repository 改成只 flush 之后这个限制不复存在。）
    """
    async with SessionLocal() as session:
        try:
            yield session
            # 请求结束时提交（读操作的空提交也走这里，本地 SQLite 空提交代价可忽略）
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
