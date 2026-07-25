"""FastAPI 依赖项"""

import logging
from typing import Annotated

from fastapi import Depends, Form, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.db import get_session
from ..db.models import User
from ..db.repositories.entry import EntryRepository
from ..db.repositories.feed import FeedRepository
from ..db.repositories.label import LabelRepository
from ..db.repositories.read_state import ReadStateRepository
from ..db.repositories.star_state import StarStateRepository
from ..db.repositories.subscription import SubscriptionRepository
from ..db.repositories.user import UserRepository
from ..services.entry import EntryService
from ..services.feed import FeedService
from ..services.label import LabelService
from ..services.read_batch import ReadBatchService
from ..services.read_state import ReadStateService
from ..services.star_state import StarStateService
from ..services.subscription import SubscriptionService
from ..services.user import UserService
from ._common import OutputType

_logger = logging.getLogger(__name__)


async def get_read_state_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReadStateService:
    return ReadStateService(repository=ReadStateRepository(session=session))


async def get_star_state_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StarStateService:
    return StarStateService(repository=StarStateRepository(session=session))


async def get_entry_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    read_state_service: Annotated[ReadStateService, Depends(get_read_state_service)],
    star_state_service: Annotated[StarStateService, Depends(get_star_state_service)],
) -> EntryService:
    return EntryService(
        repository=EntryRepository(session=session),
        read_state_service=read_state_service,
        star_state_service=star_state_service,
    )


async def get_feed_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    entry_service: Annotated[EntryService, Depends(get_entry_service)],
) -> FeedService:
    return FeedService(
        repository=FeedRepository(session=session),
        entry_service=entry_service,
    )


async def get_subscription_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    feed_service: Annotated[FeedService, Depends(get_feed_service)],
    read_state_service: Annotated[ReadStateService, Depends(get_read_state_service)],
    star_state_service: Annotated[StarStateService, Depends(get_star_state_service)],
) -> SubscriptionService:
    return SubscriptionService(
        repository=SubscriptionRepository(session=session),
        feed_service=feed_service,
        read_state_service=read_state_service,
        star_state_service=star_state_service,
    )


async def get_label_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    star_state_service: Annotated[StarStateService, Depends(get_star_state_service)],
    entry_service: Annotated[EntryService, Depends(get_entry_service)],
) -> LabelService:
    return LabelService(
        repository=LabelRepository(session=session),
        subscription_service=subscription_service,
        star_state_service=star_state_service,
        entry_service=entry_service,
    )


async def get_read_batch_service(
    read_state_service: Annotated[ReadStateService, Depends(get_read_state_service)],
    entry_service: Annotated[EntryService, Depends(get_entry_service)],
    feed_service: Annotated[FeedService, Depends(get_feed_service)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    star_state_service: Annotated[StarStateService, Depends(get_star_state_service)],
) -> ReadBatchService:
    return ReadBatchService(
        read_state_service=read_state_service,
        entry_service=entry_service,
        feed_service=feed_service,
        subscription_service=subscription_service,
        star_state_service=star_state_service,
    )


async def get_user_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    subscription_service: Annotated[
        SubscriptionService, Depends(get_subscription_service)
    ],
    label_service: Annotated[LabelService, Depends(get_label_service)],
    read_state_service: Annotated[ReadStateService, Depends(get_read_state_service)],
    star_state_service: Annotated[StarStateService, Depends(get_star_state_service)],
) -> UserService:
    return UserService(
        repository=UserRepository(session=session),
        subscription_service=subscription_service,
        label_service=label_service,
        read_state_service=read_state_service,
        star_state_service=star_state_service,
    )


async def get_current_user(
    user_service: Annotated[UserService, Depends(get_user_service)],
    authorization: Annotated[str, Header()],
    T: Annotated[str | None, Form()] = None,
) -> User:
    """FastAPI 依赖：从 Authorization 头解析当前用户"""
    if not authorization.startswith("GoogleLogin auth="):
        _logger.warning("缺少或无效的 Authorization 头: %s", authorization)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "MissingAuthHeader", "detail": "缺少 Authorization 头"},
        )
    token = authorization[len("GoogleLogin auth=") :]
    user = await user_service.verify_auth(token)
    if not user or (
        T and T not in ["", "x", token.split("/")[1] if "/" in token else ""]
    ):
        _logger.warning("认证失败: T=%s 用户=%s", T, user.username if user else None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "InvalidAuthOrT",
                "detail": "无效的 Authorization 头或 T token",
            },
        )
    _logger.info("用户 %s（%d）认证通过", user.username, user.id)
    return user


def reject_xml(output: Annotated[OutputType, Query()] = OutputType.JSON) -> None:
    """不支持 XML 输出时直接拒绝请求"""
    if output == OutputType.XML:
        _logger.warning("拒绝 XML 输出请求")
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"code": "XMLOutputUnsupported", "detail": "暂不支持 XML 输出"},
        )
