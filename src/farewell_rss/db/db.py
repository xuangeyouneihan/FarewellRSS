import logging
import os
import subprocess
from getpass import getuser

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateColumn, CreateIndex, DropIndex

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


def _restrict_to_owner(path: str, mode: int) -> None:
    """POSIX：把权限收到「只有属主可访问」"""
    try:
        os.chmod(path, mode)
    except OSError as error:
        # 只读挂载、NFS、奇怪的卷驱动……：权限收不紧不该让启动失败，但要留痕
        _logger.warning("无法收紧 %s 的权限：%s", path, error)


# 用 SID 而不是名字：内置账号的名字是**本地化**的（中文 Windows 上 Users / Everyone
# 可能显示成别的），这两个 SID 则是固定的。
_SYSTEM_SID = "*S-1-5-18"
_ADMINS_SID = "*S-1-5-32-544"


def _restrict_windows_path(path: str, suffix: str) -> None:
    """Windows：用 `icacls` 把 path 的 ACL 收成「当前账号 + SYSTEM + Administrators」

    为什么不用 `os.chmod`：Windows 上它只能改「只读」那一位，**碰不到 ACL**。

    现状是（实测）：默认位置（`%USERPROFILE%` 下）本来就只继承那三条 ACE、没有
    `BUILTIN\\Users`，所以本机其他标准账号读不到。这一手是为 `FAREWELL_RSS_DATA_DIR`
    指到共享位置准备的（D 盘根目录、被授予 Everyone 的目录、NAS/共享）。

    `/inheritance:r` 会丢掉继承来的 ACE —— 包括父目录上管理员特意加的授权（比如备份
    账号），所以成功时留一条 INFO，出问题时能查到是我们干的。
    """
    domain = os.environ.get("USERDOMAIN", "")
    username = os.environ.get("USERNAME", "")
    account = f"{domain}\\{username}" if domain and username else getuser()
    grants = (_SYSTEM_SID, _ADMINS_SID, account)
    command = [
        "icacls",
        path,
        "/inheritance:r",
        "/grant:r",
        *(f"{grant}:{suffix}" for grant in grants),
    ]
    try:
        # CREATE_NO_WINDOW：服务 /pythonw 下跑时不要弹黑框。
        # 必须用 getattr 读：这个常量**只在 Windows 上存在**，CI（Linux）上直接写属性名
        # mypy 会报 attr-defined；POSIX 上 subprocess 又要求它恰好是 0。
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as error:
        _logger.warning("无法收紧 %s 的 ACL：%s", path, error)
        return
    if result.returncode != 0:
        _logger.warning(
            "无法收紧 %s 的 ACL（icacls 退出码 %d，多半是 FAT/exFAT 卷或权限不足）：%s",
            path,
            result.returncode,
            result.stderr.decode(errors="replace").strip(),
        )
        return
    _logger.info("已把 %s 的访问权限收成「当前账号 + SYSTEM + Administrators」", path)


def restrict_data_permissions() -> None:
    """收紧数据目录（`0700` / 专属 ACL）与其中敏感文件（`0600` / 同）的权限，幂等

    数据目录里有两样不该让同机其他账号看到的东西：

    * `farewell_rss.db` —— `feeds.href` 可能带着**明文凭据**（`https://user:pass@…`）
    * `.env` —— 签名 token 的 HMAC 密钥

    POSIX 上默认 umask 会建出 `0644` / 目录 `0755`（世界可读）；Windows 的默认位置
    （`%USERPROFILE%` 下）本来就是专属的，但 `FAREWELL_RSS_DATA_DIR` 指到共享位置时
    不一定。两边都在启动时收一次，顺便把之前建出来的老文件一并收回来。
    """
    # -wal / -shm 是 WAL 模式的伴生文件，同样含数据（凭据可能还在 WAL 里）
    names = (
        "farewell_rss.db",
        "farewell_rss.db-wal",
        "farewell_rss.db-shm",
        ".env",
    )
    if os.name == "nt":
        # 目录带 (OI)(CI)：以后新建的库/env 会自动继承这一套 ACL（现有文件还得单独收）
        if os.path.isdir(DATA_DIR):
            _restrict_windows_path(DATA_DIR, "(OI)(CI)F")
        for name in names:
            path = os.path.join(DATA_DIR, name)
            if os.path.exists(path):
                _restrict_windows_path(path, "F")
        return
    if os.path.isdir(DATA_DIR):
        _restrict_to_owner(DATA_DIR, 0o700)
    for name in names:
        path = os.path.join(DATA_DIR, name)
        if os.path.exists(path):
            _restrict_to_owner(path, 0o600)


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

        def _reconcile_columns(sync_conn) -> None:
            """老库补上后来新增的列（幂等）

            理由与上面索引那段相同：`create_all` 对已存在的表整张跳过，模型里新增的列
            在老库上根本不会出现，之后任何 SELECT 都会 `no such column`。

            SQLite 的 ADD COLUMN 有限制：加不了外键、加不了 UNIQUE，也不能加「非空且
            无默认值」的列 —— 所以这里只补**可空列**；碰到非空列直接报出来，那种改动
            必须手写迁移（并且得想清楚老行填什么值）。
            """
            for table in Base.metadata.sorted_tables:
                existing = {
                    row[1]
                    for row in sync_conn.execute(
                        text(f"PRAGMA table_info({table.name})")
                    )
                }
                for column in table.columns:
                    if column.name in existing:
                        continue
                    if not column.nullable:
                        _logger.warning(
                            "表 %s 缺少非空列 %s，ADD COLUMN 补不了，需要手写迁移",
                            table.name,
                            column.name,
                        )
                        continue
                    _logger.warning(
                        "表 %s 缺少列 %s，按模型补上", table.name, column.name
                    )
                    ddl = CreateColumn(column).compile(dialect=sync_conn.dialect)
                    sync_conn.execute(
                        text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}")
                    )

        await conn.run_sync(_reconcile_indexes)
        await conn.run_sync(_reconcile_columns)

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

    # 建表之后调（那时库文件才真的存在），把权限收回来
    restrict_data_permissions()


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
