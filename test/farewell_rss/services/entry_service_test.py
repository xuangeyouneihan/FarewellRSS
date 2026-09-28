"""EntryService：把「查询串写错了」和「库真的出问题了」分开

（文件名带 service 是为了跟 db/repositories/entry_test.py 区分：测试文件是按
basename 收集的，重名会 import mismatch。）
"""

import sqlite3

from sqlalchemy.exc import OperationalError

from farewell_rss.services.entry import _is_fts5_syntax_error


def _error(message: str) -> OperationalError:
    """造一个 SQLite 报上来的 OperationalError（和实测拿到的形状一致）"""
    return OperationalError("SELECT ...", {}, sqlite3.OperationalError(message))


def test_fts5_syntax_error_is_recognized():
    """实测就这两种措辞：运算符/括号错，以及引号不闭合"""
    assert _is_fts5_syntax_error(_error('fts5: syntax error near "OR"'))
    assert _is_fts5_syntax_error(_error('fts5: syntax error near ""'))
    assert _is_fts5_syntax_error(_error('fts5: syntax error near ")"'))
    # 这一条没有 fts5 前缀，是 FTS5 解析短语时自己抛的
    assert _is_fts5_syntax_error(_error("unterminated string"))


def test_real_database_error_is_not_blamed_on_the_query():
    """跟查询无关的库错误不许被翻译成「你查询写错了」——宁可退回 500，看得见"""
    assert not _is_fts5_syntax_error(_error("no such table: entry_fts"))
    assert not _is_fts5_syntax_error(_error("database is locked"))
    assert not _is_fts5_syntax_error(_error("disk I/O error"))
    # 也不能靠错误码区分：实测两种错都是 code=1 / SQLITE_ERROR（手工构造的异常
    # 不带 sqlite_errorcode，所以这里只留注释，测量过程记在上面的注释里）
