import logging
import os

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_logger = logging.getLogger(__name__)

DATA_DIR = os.getenv("FAREWELL_RSS_DATA_DIR", "data")
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
    """创建所有表（含 FTS5 搜索索引）"""
    from .models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
    async with SessionLocal() as session:  # noqa: SIM117
        # 事务上下文管理器，自动提交或回滚
        # 读操作空提交就空提交吧，反正是自部署，数据库就在本地，空提交也快
        async with session.begin():
            yield session
