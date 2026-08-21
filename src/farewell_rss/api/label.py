import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Response, status

from ..db.models import LabelType, User
from ..services.entry import EntryService
from ..services.feed import FeedService
from ..services.label import LabelService
from ..services.read_batch import ReadBatchService
from ..services.read_state import ReadStateService
from ..services.star_state import StarStateService
from ..services.subscription import SubscriptionService
from ._common import OutputType, parse_item_ids
from .deps import (
    get_current_user,
    get_entry_service,
    get_feed_service,
    get_label_service,
    get_read_batch_service,
    get_read_state_service,
    get_star_state_service,
    get_subscription_service,
    reject_xml,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reader/api/0", tags=["label"])


@router.get("/tag/list")
async def list_labels(
    output: Annotated[OutputType, Depends(reject_xml)],
    user: Annotated[User, Depends(get_current_user)],
    label_service: Annotated[LabelService, Depends(get_label_service)],
) -> dict:
    """获取用户的所有标签"""
    labels = await label_service.list_by_user(user=user)
    result = [
        {"id": "user/-/state/com.google/reading-list"},
        {"id": "user/-/state/com.google/starred"},
    ] + [{"id": f"user/-/label/{label.name}", "type": label.type} for label in labels]
    return {"tags": result}


@router.post("/edit-tag")
async def edit_tag(
    user: Annotated[User, Depends(get_current_user)],
    entry_service: Annotated[EntryService, Depends(get_entry_service)],
    read_state_service: Annotated[ReadStateService, Depends(get_read_state_service)],
    star_state_service: Annotated[StarStateService, Depends(get_star_state_service)],
    label_service: Annotated[LabelService, Depends(get_label_service)],
    i: Annotated[list[str], Form()],
    a: Annotated[list[str] | None, Form()] = None,
    r: Annotated[list[str] | None, Form()] = None,
) -> Response:
    """编辑标签"""
    entry_ids = parse_item_ids(i)
    # 批量查询只调一次，避免在循环内反复全量查询（O(n²) 次数据库往返）
    entry_map = await entry_service.get_batch(entry_ids)
    entries = [e for eid in entry_ids if (e := entry_map.get(eid)) is not None]

    for entry in entries:
        for label_name in a or []:
            match label_name:
                case "user/-/state/com.google/read":
                    await read_state_service.upsert(
                        user, entry, timestamp=datetime.now(UTC)
                    )
                case "user/-/state/com.google/starred":
                    star_state = await star_state_service.get(user, entry)
                    await star_state_service.upsert(
                        user,
                        entry,
                        tag_id=star_state.tag_id if star_state else None,
                        timestamp=datetime.now(UTC),
                    )
                case l if l.startswith("user/-/label/"):
                    tag = await label_service.get_by_user_name_type(
                        user, l[13:], type_=LabelType.TAG
                    )
                    if not tag:
                        tag = await label_service.create(
                            user, l[13:], type_=LabelType.TAG
                        )
                    star_state = await star_state_service.get(user, entry)
                    timestamp = (
                        star_state.timestamp if star_state else datetime.now(UTC)
                    )
                    await star_state_service.upsert(
                        user,
                        entry,
                        tag_id=tag.id,
                        timestamp=timestamp,
                    )

        for label_name in r or []:
            match label_name:
                case "user/-/state/com.google/read":
                    read_state = await read_state_service.get(user, entry)
                    if read_state:
                        await read_state_service.delete(read_state)
                case "user/-/state/com.google/starred":
                    star_state = await star_state_service.get(user, entry)
                    if star_state:
                        await star_state_service.delete(star_state)
                case l if l.startswith("user/-/label/"):
                    tag = await label_service.get_by_user_name_type(
                        user, l[13:], type_=LabelType.TAG
                    )
                    if not tag:
                        continue
                    star_state = await star_state_service.get(user, entry)
                    timestamp = (
                        star_state.timestamp if star_state else datetime.now(UTC)
                    )
                    if star_state and tag and star_state.tag_id == tag.id:
                        await star_state_service.upsert(
                            user,
                            entry,
                            tag_id=None,
                            timestamp=timestamp,
                        )

    return Response("OK", media_type="text/plain")


@router.post("/enable-tag")
async def create_label(
    user: Annotated[User, Depends(get_current_user)],
    label_service: Annotated[LabelService, Depends(get_label_service)],
    s: Annotated[list[str], Form()],
    type: Annotated[list[LabelType] | None, Form()] = None,
) -> Response:
    """添加标签"""
    label_names = []
    for i in range(len(s)):
        label_id = s[i]
        if not label_id.startswith("user/-/label/"):
            _logger.warning(
                "用户 %s（%d）尝试创建无效的标签 ID: %s",
                user.username,
                user.id,
                label_id,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "InvalidLabelId",
                    "detail": "标签 ID 必须以 'user/-/label/' 开头",
                },
            )
        label_names.append(label_id[13:])

    types = type or []
    for i, name in enumerate(label_names):
        type_ = types[i] if i < len(types) else LabelType.FOLDER
        existing = await label_service.get_by_user_name_type(user, name, type_)
        if not existing:
            await label_service.create(user, name, type_)

    return Response("OK", media_type="text/plain")


@router.post("/rename-tag")
async def rename_label(
    user: Annotated[User, Depends(get_current_user)],
    label_service: Annotated[LabelService, Depends(get_label_service)],
    s: Annotated[list[str], Form()],
    dest: Annotated[list[str], Form()],
    type: Annotated[list[LabelType] | None, Form()] = None,
) -> Response:
    """重命名标签"""
    if len(s) != len(dest):
        _logger.warning(
            "用户 %s（%d）尝试重命名标签时，原标签数量 %d 与新标签数量 %d 不匹配",
            user.username,
            user.id,
            len(s),
            len(dest),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "MismatchedLabelRename",
                "detail": "标签原名和标签新名称数量不匹配",
            },
        )

    all_labels = await label_service.list_by_user(user)
    label_map = {(label.name, label.type): label for label in all_labels}

    types = type or []
    labels = []
    for i in range(len(s)):
        old_id = s[i]
        new_id = dest[i]
        if not old_id.startswith("user/-/label/") or not new_id.startswith(
            "user/-/label/"
        ):
            _logger.warning(
                "用户 %s（%d）尝试重命名标签时，原标签 ID %s 或新标签 ID %s 无效",
                user.username,
                user.id,
                old_id,
                new_id,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "InvalidLabelId",
                    "detail": "标签 ID 必须以 'user/-/label/' 开头",
                },
            )
        label = None
        type_ = types[i] if i < len(types) else None
        if type_:
            label = label_map.get((old_id[13:], type_))
        else:
            label = label_map.get((old_id[13:], LabelType.FOLDER))
            if not label:
                label = label_map.get((old_id[13:], LabelType.TAG))
        if not label:
            _logger.warning(
                "用户 %s（%d）尝试重命名标签时，原标签 %s 不存在",
                user.username,
                user.id,
                old_id[13:],
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "LabelNotFound",
                    "detail": f"标签 '{old_id[13:]}' 不存在",
                },
            )
        labels.append((label, new_id[13:]))

    existing_name_types = {(label.name, label.type) for label in all_labels}
    old_name_type_counts = {}
    new_name_type_counts = {}
    for label, new_name in labels:
        old_name_type_counts[(label.name, label.type)] = (
            old_name_type_counts.get((label.name, label.type), 0) + 1
        )
        new_name_type_counts[(new_name, label.type)] = (
            new_name_type_counts.get((new_name, label.type), 0) + 1
        )
    if any(
        (count - old_name_type_counts.get(name_type, 0) > 1)
        or (
            name_type in existing_name_types
            and count - old_name_type_counts.get(name_type, 0) > 0
        )
        for name_type, count in new_name_type_counts.items()
    ):
        _logger.warning(
            "用户 %s（%d）尝试重命名标签时，新标签名称与现有标签名称冲突",
            user.username,
            user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "LabelNameConflict",
                "detail": "标签新名称与现有标签名称冲突",
            },
        )

    existing_names = {label.name for label in all_labels}
    temp_names = set()
    while len(temp_names) < len(labels):
        temp_name = f"temp_{uuid4().hex}"
        while temp_name in existing_names or temp_name in temp_names:
            temp_name = f"temp_{uuid4().hex}"
        temp_names.add(temp_name)
    temp_names = list(temp_names)

    for i, (label, _) in enumerate(labels):
        temp_name = temp_names[i]
        await label_service.update(label, temp_name)

    for label, new_name in labels:
        updated = await label_service.update(label, new_name)
        if not updated:
            _logger.warning(
                "用户 %s（%d）尝试重命名标签时，新标签名称与现有标签名称冲突",
                user.username,
                user.id,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "LabelNameConflict",
                    "detail": "标签新名称与现有标签名称冲突",
                },
            )

    return Response("OK", media_type="text/plain")


@router.post("/disable-tag")
async def delete_label(
    user: Annotated[User, Depends(get_current_user)],
    label_service: Annotated[LabelService, Depends(get_label_service)],
    s: Annotated[list[str], Form()],
    type: Annotated[list[LabelType] | None, Form()] = None,
) -> Response:
    """删除标签"""
    labels = []
    for i in range(len(s)):
        label_id = s[i]
        if not label_id.startswith("user/-/label/"):
            _logger.warning(
                "用户 %s（%d）尝试删除标签时，遇到无效的标签 ID: %s",
                user.username,
                user.id,
                label_id,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "InvalidLabelId",
                    "detail": "标签 ID 必须以 'user/-/label/' 开头",
                },
            )
        types = type or []
        type_ = types[i] if i < len(types) else None
        label = None
        if type_:
            label = await label_service.get_by_user_name_type(
                user, label_id[13:], type_
            )
        else:
            label = await label_service.get_by_user_name_type(
                user, label_id[13:], LabelType.FOLDER
            )
            if not label:
                label = await label_service.get_by_user_name_type(
                    user, label_id[13:], LabelType.TAG
                )
        if not label:
            _logger.warning(
                "用户 %s（%d）尝试删除不存在的标签 %s",
                user.username,
                user.id,
                label_id[13:],
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "LabelNotFound",
                    "detail": f"标签 '{label_id[13:]}' 不存在",
                },
            )
        labels.append(label)

    for label in labels:
        await label_service.delete(label)

    return Response("OK", media_type="text/plain")


@router.post("/mark-all-as-read")
async def mark_all_as_read(
    user: Annotated[User, Depends(get_current_user)],
    feed_service: Annotated[FeedService, Depends(get_feed_service)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    label_service: Annotated[LabelService, Depends(get_label_service)],
    read_batch_service: Annotated[ReadBatchService, Depends(get_read_batch_service)],
    s: Annotated[str, Form()],
    type: Annotated[LabelType | None, Form()] = None,
    ts: Annotated[str | None, Form()] = None,
) -> Response:
    """将流下的所有文章标记为已读"""
    older_than_id = int(ts) if ts and ts.isdigit() else None

    if s in ["user/-/state/com.google/reading-list", "user/-/state/com.google/unread"]:
        await read_batch_service.insert_by_user(user, older_than_id)
    elif s == "user/-/state/com.google/starred":
        await read_batch_service.insert_starred_by_user(user, older_than_id)
    elif s == "user/-/state/com.google/read":
        pass
    elif s.startswith("feed/"):
        if not s[5:].isdigit():
            _logger.warning(
                "用户 %s（%d）尝试标记订阅源 %s 下的所有条目为已读时，订阅源 ID 无效",
                user.username,
                user.id,
                s[5:],
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "InvalidFeedId", "detail": "订阅源 ID 必须是数字"},
            )
        feed = await feed_service.get(int(s[5:]))
        if not feed:
            _logger.warning(
                "用户 %s（%d）尝试标记订阅源 %s 下的所有条目为已读时，未找到该订阅源",
                user.username,
                user.id,
                s[5:],
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "FeedNotFound", "detail": f"未找到订阅源: {s[5:]}"},
            )
        subscription = await subscription_service.get(user, feed)
        if not subscription:
            _logger.warning(
                "用户 %s（%d）尝试标记订阅源 %s 下的所有条目为已读时，未订阅该源",
                user.username,
                user.id,
                s[5:],
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "SubscriptionNotFound", "detail": "未订阅该源"},
            )
        await read_batch_service.insert_by_subscription(subscription, older_than_id)
    elif s.startswith("user/-/label/"):
        label = None
        if type == LabelType.TAG:
            label = await label_service.get_by_user_name_type(
                user, s[13:], LabelType.TAG
            )
        elif type == LabelType.FOLDER:
            label = await label_service.get_by_user_name_type(
                user, s[13:], LabelType.FOLDER
            )
        else:
            label = await label_service.get_by_user_name_type(
                user, s[13:], LabelType.FOLDER
            )
            if not label:
                label = await label_service.get_by_user_name_type(
                    user, s[13:], LabelType.TAG
                )
        if not label:
            _logger.warning(
                "用户 %s（%d）尝试标记文件夹/标签 %s 下的所有条目为已读时，未找到该标签",
                user.username,
                user.id,
                s[13:],
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "LabelNotFound", "detail": f"未找到标签: {s[13:]}"},
            )
        await read_batch_service.insert_by_label(label, older_than_id)
    else:
        _logger.warning(
            "用户 %s（%d）尝试标记无效的流 ID %s 下的所有条目为已读",
            user.username,
            user.id,
            s,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "InvalidStreamId", "detail": f"无效的流 ID: {s}"},
        )

    return Response("OK", media_type="text/plain")
