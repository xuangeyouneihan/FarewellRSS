"""Google Reader API 文章流端点"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from time import time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, status

from ..db.models import Entry, LabelType, User
from ..services import Filtering
from ..services.entry import EntryService
from ..services.feed import FeedService
from ..services.label import LabelService
from ..services.read_state import ReadStateService
from ..services.star_state import StarStateService
from ..services.subscription import SubscriptionService
from ..services.user import UserService
from ._common import OutputType, Sorting, parse_item_ids
from .deps import (
    get_current_user,
    get_entry_service,
    get_feed_service,
    get_label_service,
    get_read_state_service,
    get_star_state_service,
    get_subscription_service,
    get_user_service,
    reject_xml,
)

_logger = logging.getLogger(__name__)

FILTERING_MAP = {
    "user/-/state/com.google/read": Filtering.READ,
    "user/-/state/com.google/unread": Filtering.UNREAD,
    "user/-/state/com.google/starred": Filtering.STARRED,
}

router = APIRouter(prefix="/reader/api/0", tags=["stream"])


def _entry_sort_key(entry: Entry) -> tuple[int, int]:
    """排序键：(有效时间戳秒, id)。

    有效时间 = published > updated > fetched（与 repository 的 coalesce 一致）。
    id 作为次级键，保证同一秒内条目的排序与分页稳定。
    """
    effective = entry.published or entry.updated or entry.fetched
    return (int(effective.timestamp()), entry.id)


def _encode_continuation(entry: Entry) -> str:
    """continuation = 16 位 hex 时间戳 + 16 位 hex id（共 32 位 hex）"""
    ts, id_ = _entry_sort_key(entry)
    return f"{ts:016x}{id_:016x}"


def _decode_continuation(c: str) -> tuple[int, int] | None:
    """解析 continuation；格式不符时返回 None（视为无分页锚点）"""
    c = c.strip()
    if len(c) != 32:
        return None
    try:
        return (int(c[:16], 16), int(c[16:], 16))
    except ValueError:
        return None


async def _resolve_stream(
    s: str,
    type: LabelType | None,
    user: User,
    ot: int | None,
    nt: int | None,
    it: str | None,
    xt: str | None,
    r: Sorting,
    c: str | None,
    n: int,
    user_service: UserService,
    feed_service: FeedService,
    subscription_service: SubscriptionService,
    label_service: LabelService,
    star_state_service: StarStateService,
    entry_service: EntryService,
) -> tuple[list[Entry], str | None]:
    """解析流路径并返回分页后的条目列表和 continuation"""
    raw_entries: list[Entry] = []

    include = FILTERING_MAP.get(it) if it else None
    exclude = FILTERING_MAP.get(xt) if xt else None
    start = datetime.fromtimestamp(ot, tz=UTC) if ot else None
    end = datetime.fromtimestamp(nt, tz=UTC) if nt else None

    match s:
        case "user/-/state/com.google/reading-list":
            raw_entries = await user_service.list_entries(
                user=user,
                start=start,
                end=end,
                include=include,
                exclude=exclude,
            )
        case "user/-/state/com.google/starred":
            raw_entries = await user_service.list_entries(
                user=user,
                start=start,
                end=end,
                include=Filtering.STARRED,
                exclude=None,
            )
        case "user/-/state/com.google/read":
            raw_entries = await user_service.list_entries(
                user=user,
                start=start,
                end=end,
                include=Filtering.READ,
                exclude=None,
            )
        case "user/-/state/com.google/unread":
            raw_entries = await user_service.list_entries(
                user=user,
                start=start,
                end=end,
                include=Filtering.UNREAD,
                exclude=None,
            )
        case "user/-/state/farewell-rss/starred-uncategorized":
            star_states = await star_state_service.list_uncategorized(user)
            if star_states:
                entry_ids = [ss.entry_id for ss in star_states]
                raw_entries = list((await entry_service.get_batch(entry_ids)).values())
        case f if f.startswith("feed/"):
            feed = await feed_service.get(int(f[5:]))
            if not feed:
                _logger.warning(
                    "用户 %s（%d）尝试获取订阅源 %s 下的条目时，未找到该订阅源",
                    user.username,
                    user.id,
                    f[5:],
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "FeedNotFound", "detail": f"未找到订阅源: {f[5:]}"},
                )
            raw_entries = await subscription_service.list_entries(
                subscription=await subscription_service.get(user, feed),
                start=start,
                end=end,
                include=include,
                exclude=exclude,
            )
        case l if l.startswith("user/-/label/"):
            if type == LabelType.TAG:
                label = await label_service.get_by_user_name_type(
                    user, l[13:], LabelType.TAG
                )
            elif type == LabelType.FOLDER:
                label = await label_service.get_by_user_name_type(
                    user, l[13:], LabelType.FOLDER
                )
            else:
                label = await label_service.get_by_user_name_type(
                    user, l[13:], LabelType.FOLDER
                )
                if not label:
                    label = await label_service.get_by_user_name_type(
                        user, l[13:], LabelType.TAG
                    )
            if not label:
                _logger.warning(
                    "用户 %s（%d）尝试获取文件夹/标签 %s 下的条目时，未找到该文件夹/标签",
                    user.username,
                    user.id,
                    l[13:],
                )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "LabelNotFound", "detail": f"未找到标签: {l[13:]}"},
                )
            raw_entries = await label_service.list_entries(
                label=label,
                start=start,
                end=end,
                include=include,
                exclude=exclude,
            )
        case q if q.startswith("user/-/search/"):
            offset = int(c, 16) if c else 0
            raw_entries = await entry_service.search(q[14:], limit=n + 1, offset=offset)
            continuation = None
            if len(raw_entries) > n:
                continuation = f"{offset + n:016x}"
                raw_entries = raw_entries[:n]
            return raw_entries, continuation
        case _:
            _logger.warning(
                "用户 %s（%d）尝试获取无效的流 ID %s 下的条目",
                user.username,
                user.id,
                s,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "InvalidStreamID", "detail": f"无效的流 ID: {s}"},
            )

    raw_entries.sort(
        key=_entry_sort_key,
        reverse=(r != Sorting.OLDEST_FIRST),
    )

    if c:
        cursor = _decode_continuation(c)
        if cursor is not None:
            if r == Sorting.OLDEST_FIRST:
                raw_entries = [e for e in raw_entries if _entry_sort_key(e) > cursor]
            else:
                raw_entries = [e for e in raw_entries if _entry_sort_key(e) < cursor]

    continuation = None
    if len(raw_entries) > n:
        continuation = _encode_continuation(raw_entries[n - 1])
        raw_entries = raw_entries[:n]

    return raw_entries, continuation


async def _build_items(
    entries: Sequence[Entry],
    user: User,
    feed_service: FeedService,
    subscription_service: SubscriptionService,
    label_service: LabelService,
    read_state_service: ReadStateService,
    star_state_service: StarStateService,
) -> list[dict]:
    """将 Entry 列表转为 Google Reader API 格式的 items"""
    feed_map = await feed_service.get_batch([e.feed_id for e in entries])
    subscription_map = await subscription_service.get_batch(
        user, list(feed_map.values())
    )
    folder_map = await label_service.get_batch([
        s.folder_id for s in subscription_map.values() if s.folder_id is not None
    ])
    read_state_map = await read_state_service.get_batch(user, entries)
    star_state_map = await star_state_service.get_batch(user, entries)

    items: list[dict] = []
    for entry in entries:
        feed = feed_map.get(entry.feed_id)
        subscription = subscription_map.get(entry.feed_id)
        folder = (
            folder_map.get(subscription.folder_id)
            if subscription and subscription.folder_id is not None
            else None
        )

        categories = ["user/-/state/com.google/reading-list"]
        if entry.id in read_state_map:
            categories.append("user/-/state/com.google/read")
        if entry.id in star_state_map:
            categories.append("user/-/state/com.google/starred")
        if folder:
            categories.append(f"user/-/label/{folder.name}")
        if entry.id in star_state_map and star_state_map[entry.id].tag_id is not None:
            tag = await label_service.get(star_state_map[entry.id].tag_id)
            if tag:
                categories.append(f"user/-/label/{tag.name}")

        items.append({
            "id": f"tag:google.com,2005:reader/item/{entry.id:016x}",
            "crawlTimeMsec": str(int(entry.fetched.timestamp() * 1e3)),
            "timestampUsec": str(int(entry.fetched.timestamp() * 1e6)),
            "published": int(
                (entry.published or entry.updated or entry.fetched).timestamp()
            ),
            "updated": int(
                (entry.updated or entry.published or entry.fetched).timestamp()
            ),
            "title": entry.title,
            "canonical": [{"href": entry.link}] if entry.link else [],
            "alternate": [{"href": entry.link, "type": "text/html"}]
            if entry.link
            else [],
            "categories": categories,
            "origin": {
                "streamId": f"feed/{entry.feed_id}",
                "title": feed.title if feed else "",
                "htmlUrl": feed.link if feed else "",
            },
            "summary": {"content": entry.content or entry.summary or ""},
            "author": entry.author_name,
        })

    return items


@router.get("/stream/contents/{s:path}")
async def stream_contents(
    s: str,  # 流 ID，如 user/-/state/com.google/reading-list
    user: Annotated[User, Depends(get_current_user)],
    output: Annotated[OutputType, Depends(reject_xml)],
    user_service: Annotated[UserService, Depends(get_user_service)],
    feed_service: Annotated[FeedService, Depends(get_feed_service)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    label_service: Annotated[LabelService, Depends(get_label_service)],
    read_state_service: Annotated[ReadStateService, Depends(get_read_state_service)],
    star_state_service: Annotated[StarStateService, Depends(get_star_state_service)],
    entry_service: Annotated[EntryService, Depends(get_entry_service)],
    n: Annotated[int, Query(ge=1)] = 20,  # 返回的最大条目数
    r: Annotated[
        Sorting, Query()
    ] = Sorting.NEWEST_FIRST_ALT,  # 排序方式，n 和 d 表示按时间降序，o 表示按时间升序
    ot: Annotated[int | None, Query()] = None,  # 仅返回指定时间戳之后的条目，单位为秒
    nt: Annotated[int | None, Query()] = None,  # 仅返回指定时间戳之前的条目，单位为秒
    c: Annotated[
        str | None, Query()
    ] = None,  # 分页锚点：16 位 hex 时间戳 + 16 位 hex id
    xt: Annotated[str | None, Query()] = None,  # 排除指定标签的条目
    it: Annotated[str | None, Query()] = None,  # 仅包含指定标签的条目
    type: Annotated[
        LabelType | None, Query()
    ] = None,  # label 流类型：folder/tag，不传 = FOLDER 优先
) -> dict:
    entries, continuation = await _resolve_stream(
        s=s,
        type=type,
        user=user,
        ot=ot,
        nt=nt,
        it=it,
        xt=xt,
        r=r,
        c=c,
        n=n,
        user_service=user_service,
        feed_service=feed_service,
        subscription_service=subscription_service,
        label_service=label_service,
        star_state_service=star_state_service,
        entry_service=entry_service,
    )
    items = await _build_items(
        entries,
        user,
        feed_service,
        subscription_service,
        label_service,
        read_state_service,
        star_state_service,
    )
    result: dict = {"id": s, "updated": time(), "items": items}
    if continuation:
        result["continuation"] = continuation
    return result


@router.get("/stream/items/ids")
async def stream_items_ids(
    user: Annotated[User, Depends(get_current_user)],
    output: Annotated[OutputType, Depends(reject_xml)],
    user_service: Annotated[UserService, Depends(get_user_service)],
    feed_service: Annotated[FeedService, Depends(get_feed_service)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    label_service: Annotated[LabelService, Depends(get_label_service)],
    star_state_service: Annotated[StarStateService, Depends(get_star_state_service)],
    entry_service: Annotated[EntryService, Depends(get_entry_service)],
    s: Annotated[str, Query()],  # 流 ID，如 user/-/state/com.google/reading-list
    type: Annotated[
        LabelType | None, Query()
    ] = None,  # label 流类型：folder/tag，不传 = FOLDER 优先
    n: Annotated[int, Query(ge=1)] = 1000,  # 返回的最大条目数
    r: Annotated[
        Sorting, Query()
    ] = Sorting.NEWEST_FIRST_ALT,  # 排序方式，n 和 d 表示按时间降序，o 表示按时间升序
    ot: Annotated[int | None, Query()] = None,  # 仅返回指定时间戳之后的条目，单位为秒
    nt: Annotated[int | None, Query()] = None,  # 仅返回指定时间戳之前的条目，单位为秒
    c: Annotated[
        str | None, Query()
    ] = None,  # 分页锚点：16 位 hex 时间戳 + 16 位 hex id
    xt: Annotated[str | None, Query()] = None,  # 排除指定标签的条目
    it: Annotated[str | None, Query()] = None,  # 仅包含指定标签的条目
) -> dict:
    entries, continuation = await _resolve_stream(
        s=s,
        type=type,
        user=user,
        ot=ot,
        nt=nt,
        it=it,
        xt=xt,
        r=r,
        c=c,
        n=n,
        user_service=user_service,
        feed_service=feed_service,
        subscription_service=subscription_service,
        label_service=label_service,
        star_state_service=star_state_service,
        entry_service=entry_service,
    )
    result: dict = {
        "itemRefs": [{"id": str(e.id)} for e in entries],
    }
    if continuation:
        result["continuation"] = continuation
    return result


@router.post("/stream/items/contents")
async def stream_items_contents(
    user: Annotated[User, Depends(get_current_user)],
    output: Annotated[OutputType, Depends(reject_xml)],
    entry_service: Annotated[EntryService, Depends(get_entry_service)],
    feed_service: Annotated[FeedService, Depends(get_feed_service)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    label_service: Annotated[LabelService, Depends(get_label_service)],
    read_state_service: Annotated[ReadStateService, Depends(get_read_state_service)],
    star_state_service: Annotated[StarStateService, Depends(get_star_state_service)],
    i: Annotated[list[str], Form()],
) -> dict:
    entry_ids = parse_item_ids(i)
    entries = [
        e
        for eid in entry_ids
        if (e := (await entry_service.get_batch(entry_ids)).get(eid)) is not None
    ]
    items = await _build_items(
        entries,
        user,
        feed_service,
        subscription_service,
        label_service,
        read_state_service,
        star_state_service,
    )
    return {
        "id": "user/-/state/com.google/reading-list",
        "updated": time(),
        "items": items,
    }
