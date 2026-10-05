"""版本号来源：装包时取发行版元数据，没装包时退化成 0.0.0

这条兜底不是理论：`PYTHONPATH=src python -m farewell_rss`（有包上下文、没有安装记录）
下相对导入全都能过，但元数据找不到 —— 修之前 `api/auth.py` 会在类体里直接抛
`PackageNotFoundError`，把整个后端拦在启动前。
"""

import logging
from importlib.metadata import PackageNotFoundError

from farewell_rss import _version


def test_version_shape():
    """正常环境：拿到真版本号，且不带 hatch-vcs 的本地段"""
    assert _version.__version__
    assert "+" not in _version.__version__


def test_falls_back_without_installed_metadata(monkeypatch, caplog):
    """没有安装记录时退化成 0.0.0，并留下一条 warning 说明原因"""

    def _missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(_version, "version", _missing)
    caplog.set_level(logging.WARNING, logger="farewell_rss._version")

    assert _version._resolve_version() == "0.0.0"
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_consumers_use_the_same_source():
    """两个消费点（登录标识、抓取 UA）用的是同一个版本来源"""
    from farewell_rss.api.auth import _SOURCE
    from farewell_rss.feed_fetcher.feed_fetcher import _AGENT

    expected_source = f"FarewellRSS-{_version.__version__}"
    assert expected_source == _SOURCE
    assert _AGENT.startswith(f"FarewellRSS/{_version.__version__} (")
