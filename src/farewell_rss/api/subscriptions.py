"""Google Reader API 订阅相关端点"""

import logging
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Response, status

from ..db.models import LabelType, User
from ..services.feed import FeedService
from ..services.label import LabelService
from ..services.subscription import SubscriptionService
from ._common import OutputType
from .deps import (
    get_current_user,
    get_feed_service,
    get_label_service,
    get_subscription_service,
    reject_xml,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reader/api/0", tags=["subscriptions"])


@router.get("/subscription/list")
async def list_subscriptions(
    user: Annotated[User, Depends(get_current_user)],
    output: Annotated[OutputType, Depends(reject_xml)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    feed_service: Annotated[FeedService, Depends(get_feed_service)],
    label_service: Annotated[LabelService, Depends(get_label_service)],
) -> dict:
    """获取用户订阅列表"""
    subscriptions = await subscription_service.list_by_user(user=user)
    feed_map = await feed_service.get_batch([
        subscription.feed_id for subscription in subscriptions
    ])
    labels = await label_service.list_by_user(user=user)
    label_map = {label.id: label for label in labels}

    results = []
    for subscription in subscriptions:
        feed = feed_map.get(subscription.feed_id)
        if not feed:
            await subscription_service.unsubscribe(subscription)
            continue
        label = (
            label_map.get(subscription.folder_id)
            if subscription.folder_id is not None
            else None
        )
        results.append({
            "id": f"feed/{subscription.feed_id}",
            "title": subscription.title or feed.title or "",
            "categories": [{"id": f"user/-/label/{label.name}", "label": label.name}]
            if label
            else [],
            "url": feed.href,
            "htmlUrl": feed.link or "",
            "iconUrl": feed.icon or "",
        })
    return {"subscriptions": results}


class _SubscriptionEditAction(str, Enum):
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    EDIT = "edit"


@router.post("/subscription/edit")
async def edit_subscription(
    user: Annotated[User, Depends(get_current_user)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    label_service: Annotated[LabelService, Depends(get_label_service)],
    feed_service: Annotated[FeedService, Depends(get_feed_service)],
    ac: Annotated[_SubscriptionEditAction, Form()],
    s: Annotated[list[str], Form()],
    t: Annotated[list[str] | None, Form()] = None,
    a: Annotated[str | None, Form()] = None,
    r: Annotated[str | None, Form()] = None,
) -> Response:
    """编辑订阅"""
    if not all(f.startswith("feed/") for f in s):
        _logger.warning(
            "用户 %s（%d）尝试编辑订阅时，提供了无效的订阅 ID: %s",
            user.username,
            user.id,
            s,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "InvalidSubscriptionId",
                "detail": "订阅 ID 必须以 'feed/' 开头",
            },
        )
    feeds = [f[5:] for f in s]
    ts = t or []
    match ac:
        case _SubscriptionEditAction.SUBSCRIBE:
            if not all(f.startswith(("http://", "https://")) for f in feeds):
                _logger.warning(
                    "用户 %s（%d）尝试订阅时，提供了无效的订阅 URL: %s",
                    user.username,
                    user.id,
                    feeds,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "InvalidSubscribeFeedId",
                        "detail": "订阅时订阅 ID 中的 feed 部分必须是 URL",
                    },
                )
            label = None
            if not r and a and a.startswith("user/-/label/"):
                label = await label_service.get_by_user_name_type(
                    user, a[13:], LabelType.FOLDER
                )
            for i in range(len(feeds)):
                feed_href = feeds[i]
                title = ts[i] if i < len(ts) and ts[i] else None
                await subscription_service.subscribe(
                    user=user,
                    feed_href=feed_href,
                    title=title,
                    folder_id=label.id if label else None,
                )
        case _SubscriptionEditAction.UNSUBSCRIBE:
            if not all(f.isnumeric() for f in feeds):
                _logger.warning(
                    "用户 %s（%d）尝试退订时，提供了无效的订阅 ID: %s",
                    user.username,
                    user.id,
                    feeds,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "InvalidUnsubscribeFeedId",
                        "detail": "退订时订阅 ID 中的 feed 部分必须是数字",
                    },
                )
            for f in feeds:
                feed = await feed_service.get(int(f))
                if not feed:
                    continue
                subscription = await subscription_service.get(user, feed=feed)
                if subscription:
                    await subscription_service.unsubscribe(subscription)
        case _SubscriptionEditAction.EDIT:
            if not all(f.isnumeric() for f in feeds):
                _logger.warning(
                    "用户 %s（%d）尝试编辑订阅时，提供了无效的订阅 ID: %s",
                    user.username,
                    user.id,
                    feeds,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "InvalidEditFeedId",
                        "detail": "编辑时订阅 ID 中的 feed 部分必须是数字",
                    },
                )
            for i in range(len(feeds)):
                feed = await feed_service.get(int(feeds[i]))
                if not feed:
                    continue
                subscription = await subscription_service.get(user, feed=feed)
                if not subscription:
                    continue
                title = ts[i] if i < len(ts) and ts[i] else None
                if title is None:
                    # 未提供 t 时保留原自定义标题，避免归类/移动操作误清空标题
                    title = subscription.title
                folder_id = None
                if not r:
                    folder_id = subscription.folder_id
                if a and a.startswith("user/-/label/"):
                    label = await label_service.get_by_user_name_type(
                        user, a[13:], LabelType.FOLDER
                    )
                    if label:
                        folder_id = label.id
                await subscription_service.update(
                    user=user,
                    feed=feed,
                    title=title,
                    folder_id=folder_id,
                )
    return Response(content="OK", media_type="text/plain")


@router.post("/subscription/quickadd")
async def quickadd_subscription(
    user: Annotated[User, Depends(get_current_user)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    feed_service: Annotated[FeedService, Depends(get_feed_service)],
    quickadd: Annotated[str, Form()],
):
    """快速添加订阅"""
    try:
        if not quickadd.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "InvalidSubscribeFeedId",
                    "detail": "订阅时订阅 ID 中的 feed 部分必须是 URL",
                },
            )
        # 直接订阅并拿回 Subscription；不要用原始 URL 反查，feedparser 解析出的
        # href 可能是重定向后的最终地址，与用户输入不一致
        subscription = await subscription_service.subscribe(
            user=user,
            feed_href=quickadd,
        )
    except HTTPException as e:
        _logger.warning(
            "用户 %s（%d）尝试快速添加订阅 %s 时，发生错误: %s",
            user.username,
            user.id,
            quickadd,
            e.detail,
        )
        error = {}
        if isinstance(e.detail, dict):
            error = {
                "code": e.detail.get("code", "QuickAddFailed"),
                "detail": e.detail.get(
                    "detail",
                    f"无法添加订阅: {quickadd}，错误: {e.detail.get('detail', str(e))}",
                ),
            }
        elif isinstance(e.detail, str):
            error = {
                "code": "QuickAddFailed",
                "detail": f"无法添加订阅: {quickadd}，错误: {e.detail}",
            }
        else:
            error = {
                "code": "QuickAddFailed",
                "detail": f"无法添加订阅: {quickadd}，错误: {e!s}",
            }
        raise HTTPException(
            status_code=e.status_code,
            detail={
                "numResults": 0,
                "error": error,
            },
        )
    except Exception as e:
        _logger.exception(
            "用户 %s（%d）尝试快速添加订阅 %s 时，发生未预期的错误",
            user.username,
            user.id,
            quickadd,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "numResults": 0,
                "error": {
                    "code": type(e).__name__,
                    "detail": f"无法添加订阅: {quickadd}, 错误: {e!s}",
                },
            },
        )
    else:
        feed = await feed_service.get(subscription.feed_id)
        if feed:
            return {
                "numResults": 1,
                "query": feed.href,
                "streamId": f"feed/{subscription.feed_id}",
                "streamName": subscription.title or feed.title or "",
            }
        _logger.error(
            "用户 %s（%d）快速添加订阅 %s 后未能确认订阅源，subscription=%s",
            user.username,
            user.id,
            quickadd,
            subscription,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "numResults": 0,
                "error": {
                    "code": "QuickAddFailed",
                    "detail": f"无法添加订阅: {quickadd}",
                },
            },
        )
