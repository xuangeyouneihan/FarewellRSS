"""条目 ID 的解析。

`i` 参数在客户端之间至少有两种发法（都是真实客户端的行为，不是我猜的）：

* **Reeder** 发**不带 `tag:google.com,2005:reader/item/` 前缀的 16 位 hex**
  （`000000000000001c`）—— 实测它这么发，我们回 400 之后它就陷入重试，一篇文章都同步
  不出来。FreshRSS 因为用 `hex2dec(basename($e_id))` 而天然容忍这种写法。
* **ReadYou** 原样回传 `stream/items/ids` 给它的**十进制**短格式（`28`）。
"""

import pytest
from fastapi import HTTPException

from farewell_rss.api._common import parse_item_ids


def test_accepts_long_form():
    assert parse_item_ids(["tag:google.com,2005:reader/item/000000000000001c"]) == [28]


def test_accepts_bare_hex_without_prefix():
    """Reeder 的发法：不带前缀的 16 位 hex（回归测试）"""
    assert parse_item_ids(["000000000000001c"]) == [28]
    assert parse_item_ids(["fb115bd6d34a8e9f"]) == [0xFB115BD6D34A8E9F]


def test_accepts_decimal_short_form():
    assert parse_item_ids(["28"]) == [28]
    assert parse_item_ids(["1705042807944418"]) == [1705042807944418]


def test_hex_that_looks_decimal_is_still_hex():
    """全数字但带前导零 → 按 hex 算

    十进制 id 不会以 0 开头，而 16 位 hex 不带前缀时必然有前导零，所以这条规则不会
    把真正的十进制 id 误判成 hex（与 FreshRSS 的 `$e_id[0] === '0'` 一致）。
    """
    assert parse_item_ids(["0000000000000012"]) == [18]


def test_prefix_is_case_insensitive():
    assert parse_item_ids(["TAG:GOOGLE.COM,2005:READER/ITEM/000000000000001c"]) == [28]


def test_long_and_short_forms_can_be_mixed():
    assert parse_item_ids([
        "28",
        "tag:google.com,2005:reader/item/000000000000001c",
    ]) == [
        28,
        28,
    ]


@pytest.mark.parametrize(
    "bad", ["", "不是 id", "tag:google.com,2005:reader/item/", "zz"]
)
def test_invalid_ids_are_400(bad):
    with pytest.raises(HTTPException) as error:
        parse_item_ids([bad])

    assert error.value.status_code == 400
    assert error.value.detail["code"] == "InvalidItemId"
