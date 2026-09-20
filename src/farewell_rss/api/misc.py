"""Google Reader API 杂项端点"""

import logging
import xml.etree.ElementTree as ET
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..db.models import LabelType, User
from ..services.feed import FeedService
from ..services.label import LabelService
from ..services.read_state import ReadStateService
from ..services.subscription import SubscriptionService
from ._common import OutputType
from .deps import (
    get_current_user,
    get_feed_service,
    get_label_service,
    get_read_state_service,
    get_subscription_service,
    reject_xml,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reader/api/0", tags=["misc"])


@router.get("/unread-count")
async def unread_count(
    user: Annotated[User, Depends(get_current_user)],
    output: Annotated[OutputType, Depends(reject_xml)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    read_state_service: Annotated[ReadStateService, Depends(get_read_state_service)],
    label_service: Annotated[LabelService, Depends(get_label_service)],
) -> dict:
    """获取用户未读计数

    计数全部在 SQL 里聚合（LEFT JOIN 反连接 + GROUP BY），这里只做
    「按文件夹汇总」这一层很轻的收尾：参与汇总的只有未读 > 0 的源。
    """
    subscriptions = await subscription_service.list_by_user(user=user)
    labels = await label_service.list_by_user(user=user)
    label_map = {label.id: label for label in labels}
    unread_by_feed = await read_state_service.unread_by_feed(user)
    unread_by_tag = await read_state_service.unread_by_tag(user)

    feed_unread_counts: list[dict] = []
    # folder_id -> [未读数, 最新未读条目 id]
    folder_totals: dict[int, list[int]] = {}
    total_unread = 0
    overall_newest_id = 0

    for sub in subscriptions:
        counts = unread_by_feed.get(sub.feed_id)
        if counts is None:
            continue  # 没有未读的源不出现在结果里
        unread, newest_id = counts

        total_unread += unread
        overall_newest_id = max(overall_newest_id, newest_id)
        feed_unread_counts.append({
            "id": f"feed/{sub.feed_id}",
            "count": unread,
            "newestItemTimestampUsec": str(newest_id),
        })

        if sub.folder_id is not None and sub.folder_id in label_map:
            folder_total = folder_totals.setdefault(sub.folder_id, [0, 0])
            folder_total[0] += unread
            folder_total[1] = max(folder_total[1], newest_id)

    folder_unread_counts = [
        {
            "id": f"user/-/label/{label_map[folder_id].name}",
            "count": folder_total[0],
            "newestItemTimestampUsec": str(folder_total[1]),
        }
        for folder_id, folder_total in folder_totals.items()
    ]
    tag_unread_counts = [
        {
            "id": f"user/-/label/{label_map[tag_id].name}",
            "count": unread,
            "newestItemTimestampUsec": str(newest_id),
        }
        for tag_id, (unread, newest_id) in unread_by_tag.items()
        if tag_id in label_map
    ]

    unread_counts = (
        [
            {
                "id": "user/-/state/com.google/reading-list",
                "count": total_unread,
                "newestItemTimestampUsec": str(overall_newest_id),
            }
        ]
        + feed_unread_counts
        + folder_unread_counts
        + tag_unread_counts
    )  # tag 的计数永远在 folder 的计数之后，以区分同名 folder 和 tag

    return {"max": total_unread, "unreadcounts": unread_counts}


@router.get("/subscription/export")
async def export_opml(
    user: Annotated[User, Depends(get_current_user)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    feed_service: Annotated[FeedService, Depends(get_feed_service)],
    label_service: Annotated[LabelService, Depends(get_label_service)],
) -> Response:
    """导出 OPML"""
    subscriptions = await subscription_service.list_by_user(user)
    feed_map = await feed_service.get_batch([s.feed_id for s in subscriptions])
    folder_labels = [
        label
        for label in await label_service.list_by_user(user)
        if label.type == LabelType.FOLDER
    ]
    folder_map = {label.id: label for label in folder_labels}

    # 按文件夹分组
    grouped: dict[int | None, list] = {None: []}
    for folder_id in folder_map:
        grouped[folder_id] = []
    for sub in subscriptions:
        feed = feed_map.get(sub.feed_id)
        if not feed:
            continue
        fid = sub.folder_id if sub.folder_id in grouped else None
        grouped[fid].append((sub, feed))

    opml = ET.Element("opml", version="1.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "FarewellRSS 订阅列表"
    body = ET.SubElement(opml, "body")

    for folder_id, items in grouped.items():
        if not items:
            continue
        if folder_id is None:
            for sub, feed in items:
                _make_outline(body, sub, feed)
        else:
            folder_el = ET.SubElement(body, "outline", text=folder_map[folder_id].name)
            for sub, feed in items:
                _make_outline(folder_el, sub, feed)

    xml = ET.tostring(opml, encoding="unicode", xml_declaration=True)
    filename = f"{user.username}.opml"
    encoded_filename = quote(filename, safe="")
    disposition = (
        f'attachment; filename="{filename}"'
        if encoded_filename == filename
        else f"attachment; filename*=UTF-8''{encoded_filename}"
    )
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": disposition},
    )


def _make_outline(parent: ET.Element, subscription, feed) -> None:
    attrs = {
        "text": subscription.title or feed.title or "",
        "title": subscription.title or feed.title or "",
        "type": "rss",
        "xmlUrl": feed.href,
    }
    if feed.link:
        attrs["htmlUrl"] = feed.link
    ET.SubElement(parent, "outline", **attrs)


@router.post("/subscription/import")
async def import_opml(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    label_service: Annotated[LabelService, Depends(get_label_service)],
) -> Response:
    """导入 OPML"""
    body = await request.body()
    if not body:
        _logger.warning(
            "用户 %s（%d）尝试导入 OPML 时，上传的文件为空",
            user.username,
            user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EmptyBody", "detail": "请求体为空"},
        )
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        _logger.warning(
            "用户 %s（%d）尝试导入 OPML 时，上传的文件不是有效的 XML",
            user.username,
            user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "InvalidOPML", "detail": "无效的 OPML XML"},
        )

    async def _import_outlines(parent_el: ET.Element, folder_id: int | None):
        for outline in parent_el.findall("outline"):
            xml_url = outline.get("xmlUrl") or outline.get("xmlurl")
            if xml_url:
                title = outline.get("title") or outline.get("text") or ""
                await subscription_service.subscribe(
                    user=user,
                    feed_href=xml_url,
                    title=title,
                    folder_id=folder_id,
                )
            else:
                name = outline.get("title") or outline.get("text") or ""
                children = outline.findall("outline")
                if name and children:
                    folder = await label_service.get_by_user_name_type(
                        user, name, LabelType.FOLDER
                    )
                    if not folder:
                        folder = await label_service.create(
                            user, name, LabelType.FOLDER
                        )
                    await _import_outlines(outline, folder.id)
                else:
                    await _import_outlines(outline, folder_id)

    # 标准 OPML 的 outline 位于 <opml><body> 下；兼容旧的根级 outline 输入。
    body_el = root.find("body") if root.tag.lower() == "opml" else None
    await _import_outlines(body_el if body_el is not None else root, None)
    return Response("OK", media_type="text/plain")
