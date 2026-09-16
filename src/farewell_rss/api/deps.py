"""FastAPI 依赖项

service 图的装配在 factory.build_services（组合根）里，这里只是把它拆成
`Depends(get_xxx_service)` 形式的便捷入口，端点和依赖注入的写法都不用变。
"""

import logging
from typing import Annotated

from fastapi import Depends, Form, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.db import get_session
from ..db.models import User
from ..factory import ServiceBundle, build_services
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


async def get_services(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ServiceBundle:
    """装配整套 service：同一个请求内共享同一个 session 与同一批实例"""
    return build_services(session)


async def get_read_state_service(
    services: Annotated[ServiceBundle, Depends(get_services)],
) -> ReadStateService:
    return services.read_state


async def get_star_state_service(
    services: Annotated[ServiceBundle, Depends(get_services)],
) -> StarStateService:
    return services.star_state


async def get_entry_service(
    services: Annotated[ServiceBundle, Depends(get_services)],
) -> EntryService:
    return services.entry


async def get_feed_service(
    services: Annotated[ServiceBundle, Depends(get_services)],
) -> FeedService:
    return services.feed


async def get_subscription_service(
    services: Annotated[ServiceBundle, Depends(get_services)],
) -> SubscriptionService:
    return services.subscription


async def get_label_service(
    services: Annotated[ServiceBundle, Depends(get_services)],
) -> LabelService:
    return services.label


async def get_read_batch_service(
    services: Annotated[ServiceBundle, Depends(get_services)],
) -> ReadBatchService:
    return services.read_batch


async def get_user_service(
    services: Annotated[ServiceBundle, Depends(get_services)],
) -> UserService:
    return services.user


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
