"""Google Reader API 共享定义"""

import logging
from enum import Enum

from fastapi import HTTPException, status

_logger = logging.getLogger(__name__)


class OutputType(str, Enum):
    """订阅列表输出格式"""

    XML = "xml"
    JSON = "json"


class Sorting(str, Enum):
    OLDEST_FIRST = "o"
    NEWEST_FIRST = "n"
    NEWEST_FIRST_ALT = "d"


def parse_item_ids(raw_ids: list[str]) -> list[int]:
    """解析 Google Reader 条目 ID

    三种写法都收，对齐 FreshRSS —— 它的 `stream/items/contents` 是
    `hex2dec(basename($e_id))`，三种全认：

    * 长格式：`tag:google.com,2005:reader/item/000000000000001c`（item 的 `id`）；
    * **不带前缀的 hex**：`000000000000001c` —— **Reeder 回传的就是这种**（不收它
      会回 400，然后客户端陷入重试，一篇文章都同步不出来）；
    * 纯十进制：`28`（Google Reader 的 short form，ReadYou 等用这种）。

    十六进制/十进制的歧义按 FreshRSS 的规则消：**全数字且不以 `0` 开头**才算十进制。
    自增 id 的十进制写法不会以 0 开头；而 16 位 hex 一旦不带前缀又全数字，就必然有
    前导零（我们发的 id 带前缀时不会走到这个分支）。
    """
    ids: list[int] = []
    for item_id in raw_ids:
        try:
            ids.append(_parse_item_id(item_id))
        except ValueError:
            _logger.warning("无效的条目 ID: %s", item_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "InvalidItemId", "detail": f"无效的条目 ID: {item_id}"},
            )
    return ids


_TAG_ITEM_PREFIX = "tag:google.com,2005:reader/item/"


def _parse_item_id(item_id: str) -> int:
    """单个 id → 自增整数；解不出来抛 `ValueError`（由调用方转 400）"""
    if item_id.lower().startswith(_TAG_ITEM_PREFIX):
        return int(item_id[len(_TAG_ITEM_PREFIX) :], 16)
    if item_id.isascii() and item_id.isdigit() and not item_id.startswith("0"):
        return int(item_id)
    # 剩下的都当十六进制：既包括不以 0 开头的裸 hex，也包括带前导零的（见上面注释）
    return int(item_id, 16)
