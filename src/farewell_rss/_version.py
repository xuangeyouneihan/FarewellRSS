"""版本号的唯一来源

`importlib.metadata` 读的是**安装记录**（`site-packages` 里的 dist-info），不是源码树：
把 `src/` 挂到 `sys.path` 上直接跑（`PYTHONPATH=src python -m farewell_rss`）时，相对
导入全都能过 —— 相对导入只要求「包的父目录在 `sys.path` 上、且以包的形式导入」—— 但
元数据一个都找不到。

而用到版本号的地方都在启动链上（`api/auth.py` 的请求模型默认值、抓取用的 UA），让它抛
出去等于整个后端起不来：为了一个对外展示用的版本号，代价完全不成比例。所以这里兜到
`0.0.0`，并且只在**真的取不到**时记一条 warning（正常环境永远不会出现）。
"""

import logging
from importlib.metadata import PackageNotFoundError, version

_logger = logging.getLogger(__name__)

_PACKAGE_NAME = "farewell-rss"


def _resolve_version() -> str:
    """取发行版版本号；没装包（拿不到元数据）时退化成 `0.0.0`"""

    try:
        # 去掉 hatch-vcs 的本地段（`.dev3+g9eb610b18`）：对外标识里没必要带构建信息
        return version(_PACKAGE_NAME).split("+")[0]
    except PackageNotFoundError:
        _logger.warning(
            "拿不到 %s 的发行版元数据（没装包？），版本号按 0.0.0 处理", _PACKAGE_NAME
        )
        return "0.0.0"


__version__ = _resolve_version()
