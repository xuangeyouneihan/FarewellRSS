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
    """解析 Google Reader 条目 ID（支持 hex 和十进制）"""
    ids: list[int] = []
    for item_id in raw_ids:
        try:
            if item_id.startswith("tag:google.com,2005:reader/item/"):
                ids.append(int(item_id[32:], 16))
            else:
                ids.append(int(item_id))
        except ValueError:
            _logger.warning("无效的条目 ID: %s", item_id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "InvalidItemId", "detail": f"无效的条目 ID: {item_id}"},
            )
    return ids
