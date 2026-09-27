"""`IN (...)` 的参数个数上限，以及按批拆分 id 列表的工具。

SQLite 用 SQLITE_MAX_VARIABLE_NUMBER 限制一条 SQL 能绑定的参数个数（3.32 起默认
32766），而 `IN (...)` 会把列表里的每一项绑成一个参数 —— 列表一大就是
`OperationalError: too many SQL variables`（实测 32767 个就炸）。所以凡是拿「一批
id」去查、去删、去统计的地方，都要按批拆开。

这里只管「一条语句能塞多少参数」，不管业务上「一批有多大」。
"""

from collections.abc import Iterator

# 不贴着 32766 走：给「以后往同一条语句里再加参数」留余量，单条语句也不至于大得
# 离谱。1000 这个量级对 SQLite 完全无感（批量操作基本都在后台任务里，多几条语句
# 无所谓）。
MAX_IDS_PER_STATEMENT = 1000


def chunked[T](items: list[T], size: int | None = None) -> Iterator[list[T]]:
    """把列表切成若干段，每段都能安全地喂给 `IN (...)`。

    `size` 的默认值必须在这里解析、**不能写成参数默认值**：参数默认值在 def 时就
    绑定了，monkeypatch 本模块的常量将影响不到它，而测试正是靠把批次改小来跑分批
    分支的。
    """
    step = MAX_IDS_PER_STATEMENT if size is None else size
    for start in range(0, len(items), step):
        yield items[start : start + step]
