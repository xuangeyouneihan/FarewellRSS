"""Google Reader API 文章流端点"""

import logging
from datetime import UTC, datetime
from time import time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, status

from ..db.models import Entry, LabelType, User
from ..enums import Filtering, SortOrder
from ..services.entry import EntryService
from ..services.exceptions import InvalidSearchQueryError
from ..services.feed import FeedService
from ..services.label import LabelService
from ..services.read_state import ReadStateService
from ..services.star_state import StarStateService
from ..services.subscription import SubscriptionService
from ._common import OutputType, Sorting, parse_item_ids
from .deps import (
    get_current_user,
    get_entry_service,
    get_feed_service,
    get_label_service,
    get_read_state_service,
    get_star_state_service,
    get_subscription_service,
    reject_xml,
)

_logger = logging.getLogger(__name__)


def _now() -> int:
    """当前时间（**整秒**）

    `updated` 是 Unix 秒，规范里是整数：FreshRSS 发的是 `time()`。这里必须 `int()` 掉
    小数部分 —— Python 的 `time()` 是浮点，带小数点的数字会被 Gson 按 `Long` 解析时
    **直接抛异常**（`Expected a long but was 1791284278.015`），而客户端的重试逻辑会把它
    当成「这个请求失败了」：实测 ReadYou 就是这样 —— 我们每次都回 200、字段全对，但它
    每轮 sync 都在重登 + 重发同一批 id，最后一篇文章都存不进去。
    """
    return int(time())


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
    feed_service: FeedService,
    subscription_service: SubscriptionService,
    label_service: LabelService,
    entry_service: EntryService,
) -> tuple[list[Entry], str | None]:
    """解析流路径并返回分页后的条目列表和 continuation

    过滤、排序、分页都由各条流自己下推到 SQL，这里只负责把多取的那一条切掉、
    生成 continuation。
    """
    include = FILTERING_MAP.get(it) if it else None
    exclude = FILTERING_MAP.get(xt) if xt else None
    start = datetime.fromtimestamp(ot, tz=UTC) if ot is not None else None
    end = datetime.fromtimestamp(nt, tz=UTC) if nt is not None else None

    # continuation 解出来就是 (秒级时间戳, 条目 id)。repository 的排序、过滤、游标
    # 比较全在「秒」这一层，所以原样下传即可，不必再绕成 datetime——绕一圈不仅把秒
    # 又转回来，还得依赖 sqlite3 那个 3.12 起已废弃的 datetime 适配器。
    cursor = _decode_continuation(c) if c else None

    entries: list[Entry]
    sorting = (
        SortOrder.DESCENDING
        if r == Sorting.NEWEST_FIRST or r == Sorting.NEWEST_FIRST_ALT
        else SortOrder.ASCENDING
    )

    match s:
        case "user/-/state/com.google/reading-list":
            entries = await entry_service.list_reading_list(
                user_id=user.id,
                start=start,
                end=end,
                include=include,
                exclude=exclude,
                sorting=sorting,
                cursor=cursor,
                limit=n + 1,
            )
        case "user/-/state/com.google/starred":
            entries = await entry_service.list_starred(
                user_id=user.id,
                start=start,
                end=end,
                include=include,
                exclude=exclude,
                sorting=sorting,
                cursor=cursor,
                limit=n + 1,
            )
        case "user/-/state/com.google/read":
            entries = await entry_service.list_read(
                user_id=user.id,
                start=start,
                end=end,
                include=include,
                exclude=exclude,
                sorting=sorting,
                cursor=cursor,
                limit=n + 1,
            )
        case "user/-/state/com.google/unread":
            entries = await entry_service.list_reading_list(
                user_id=user.id,
                start=start,
                end=end,
                include=Filtering.UNREAD,
                exclude=exclude,
                sorting=sorting,
                cursor=cursor,
                limit=n + 1,
            )
        case "user/-/state/farewell-rss/starred-uncategorized":
            entries = await entry_service.list_starred(
                user_id=user.id,
                start=start,
                end=end,
                include=include,
                exclude=exclude,
                sorting=sorting,
                cursor=cursor,
                limit=n + 1,
                uncategorized=True,
            )
        case "user/-/state/farewell-rss/history":
            entries = await entry_service.list_read(
                user_id=user.id,
                start=start,
                end=end,
                include=include,
                exclude=exclude,
                sorting=sorting,
                cursor=cursor,
                limit=n + 1,
                history=True,
            )
        case f if f.startswith("feed/"):
            feed = await feed_service.get(int(f[5:]))
            if not feed or (await subscription_service.get(user, feed)) is None:
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
            entries = await entry_service.list_by_feed(
                feed,
                user_id=user.id,
                start=start,
                end=end,
                include=include,
                exclude=exclude,
                sorting=sorting,
                cursor=cursor,
                limit=n + 1,
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
            # 标签流 = 带这个标签的收藏 → 和 starred 流一样不按订阅过滤；
            # 文件夹流 = 该文件夹下的订阅 → 和 reading-list 一样按订阅过滤
            if label.type == LabelType.TAG:
                entries = await entry_service.list_starred(
                    user_id=user.id,
                    start=start,
                    end=end,
                    include=include,
                    exclude=exclude,
                    sorting=sorting,
                    cursor=cursor,
                    limit=n + 1,
                    tag_id=label.id,
                )
            else:
                entries = await entry_service.list_reading_list(
                    user_id=user.id,
                    start=start,
                    end=end,
                    include=include,
                    exclude=exclude,
                    sorting=sorting,
                    cursor=cursor,
                    limit=n + 1,
                    folder_id=label.id,
                )
        case q if q.startswith("user/-/search/"):
            # 搜索走 FTS5 的 rank 排序 + OFFSET 分页，和上面那套 keyset 分页不是一回事，
            # 所以自己算 continuation 并提前返回
            offset = int(c, 16) if c else 0
            try:
                found = await entry_service.search(
                    q[14:], user.id, limit=n + 1, offset=offset
                )
            except InvalidSearchQueryError as e:
                _logger.warning(
                    "用户 %s（%d）搜索时查询串语法错误：%s",
                    user.username,
                    user.id,
                    q[14:],
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    # 不能用 type(e)：本函数有个叫 type 的查询参数，把内置 type 遮蔽了
                    detail={"code": e.__class__.__name__, "detail": str(e)},
                ) from e
            continuation = None
            if len(found) > n:
                continuation = f"{offset + n:016x}"
                found = found[:n]
            return found, continuation
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

    # 各条流都已经把过滤、排序、游标下推到 SQL 了，这里只剩「多取一条判断还有没有
    # 下一页」：continuation 取这一页的最后一条，下一页从它后面接着取。
    continuation = None
    if len(entries) > n:
        continuation = _encode_continuation(entries[n - 1])
        entries = entries[:n]

    return entries, continuation


async def _build_items(
    entries: list[Entry],
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

    # **可见性过滤**：只给「订阅了这个源」或「这个条目有自己的已读/收藏记录」的条目。
    #
    # 这里是非做不可的：`stream/items/contents` 收的是**任意 entry id**，而 id 是自增
    # 整数（`tag:google.com,2005:reader/item/{entry.id:016x}` 就是它的十六进制）——
    # 不校验的话，按 id 枚举就能读到别人私有源里的正文。
    #
    # 为什么不能只看订阅：starred / 已读历史里会有**退订后留下**的条目（prune_orphan_feeds
    # 对有记录保护的条目会保留），那些是用户自己的数据，不该因为退订就取不回来。
    visible = [
        entry
        for entry in entries
        if entry.feed_id in subscription_map
        or entry.id in read_state_map
        or entry.id in star_state_map
    ]
    if len(visible) != len(entries):
        _logger.debug(
            "请求 %d 条条目，其中 %d 条不在该用户可见范围内，已跳过",
            len(entries),
            len(entries) - len(visible),
        )
    entries = visible

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
        # 取出局部变量再判空：直接写 star_state_map[entry.id].tag_id，mypy 没法
        # 穿过字典取值把 int | None 收窄成 int
        star_state = star_state_map.get(entry.id)
        if star_state is not None and star_state.tag_id is not None:
            tag = await label_service.get(star_state.tag_id)
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
        feed_service=feed_service,
        subscription_service=subscription_service,
        label_service=label_service,
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
    result: dict = {"id": s, "updated": _now(), "items": items}
    if continuation:
        result["continuation"] = continuation
    return result


@router.get("/stream/items/ids")
async def stream_items_ids(
    user: Annotated[User, Depends(get_current_user)],
    output: Annotated[OutputType, Depends(reject_xml)],
    feed_service: Annotated[FeedService, Depends(get_feed_service)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    label_service: Annotated[LabelService, Depends(get_label_service)],
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
        feed_service=feed_service,
        subscription_service=subscription_service,
        label_service=label_service,
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
    entry_map = await entry_service.get_batch(entry_ids)
    entries = [e for eid in entry_ids if (e := entry_map.get(eid)) is not None]
    # 客户端「拿不到文章」时，这一行能把问题分到三段中的哪一段：
    #   收到 N 个 / 解析出 M 个（差集 → `i` 的格式不认）
    #   命中 K 条（差集 → 这些 id 在库里根本不存在）
    #   再往下被可见性过滤掉的条数，由 `_build_items` 那条 debug 解释
    _logger.debug(
        "stream/items/contents：收到 %d 个 id，解析出 %d 个，命中 %d 条条目，样例 %s",
        len(i),
        len(entry_ids),
        len(entries),
        [f"{entry_id:016x}" for entry_id in entry_ids[:5]],
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
    return {
        "id": "user/-/state/com.google/reading-list",
        "updated": _now(),
        "items": items,
    }
