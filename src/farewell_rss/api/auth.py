import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Response,
    status,
)
from pydantic import BaseModel

from ..api.deps import get_current_user, get_user_service
from ..db.models import User
from ..services.exceptions import (
    LastAdminDeletionError,
    RegisterExistingUserError,
    ServiceError,
)
from ..services.user import UserService

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


def _get_source() -> str:
    """返回RSS服务标识，带版本号"""
    from importlib.metadata import version

    return f"FarewellRSS-{version('farewell_rss')}"


class LoginParams(BaseModel):
    Email: str  # 说是邮箱但实际上是用户名
    Password: str  # 密码
    accountType: str = "GOOGLE"  # 固定
    service: str = "reader"  # 固定
    source: str = _get_source()  # RSS 服务标识


async def _sign_in(login_params: LoginParams, user_service: UserService) -> Response:
    """处理登录请求，返回认证令牌，GET 和 POST 的登录都会调用这个函数"""
    user = await user_service.authenticate(login_params.Email, login_params.Password)
    if not user:
        # 此处不记日志，因为日志在 UserService.authenticate 中已经记录了认证失败的详细信息
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "InvalidCredentials", "detail": "用户名或密码错误"},
        )
    token = user_service.generate_auth(user)
    return Response(
        content=f"SID={token}\nLSID=null\nAuth={token}",
        media_type="text/plain",
    )


@router.get("/accounts/ClientLogin", status_code=status.HTTP_200_OK)
async def sign_in_get(
    user_service: Annotated[UserService, Depends(get_user_service)],
    login_params: Annotated[LoginParams, Query()],
) -> Response:
    """GET 方式，调用 _sign_in 处理登录请求，返回认证令牌"""
    return await _sign_in(login_params, user_service)


@router.post("/accounts/ClientLogin", status_code=status.HTTP_200_OK)
async def sign_in_post(
    user_service: Annotated[UserService, Depends(get_user_service)],
    login_params: Annotated[LoginParams, Form()],
) -> Response:
    """POST 方式，调用 _sign_in 处理登录请求，返回认证令牌"""
    return await _sign_in(login_params, user_service)


class RegisterParams(LoginParams):
    """注册参数，继承自 LoginParams，增加 friendly_name"""

    friendly_name: str | None = None  # 昵称


async def _register(
    register_params: RegisterParams, user_service: UserService
) -> Response:
    """处理注册请求，返回认证令牌，GET 和 POST 的注册都会调用这个函数"""
    try:
        user = await user_service.register(
            register_params.Email,
            register_params.Password,
            register_params.friendly_name,
        )
        token = user_service.generate_auth(user)
        return Response(
            content=f"SID={token}\nLSID=null\nAuth={token}",
            media_type="text/plain",
        )
    # 此处不记日志，因为日志在 UserService.register 中已经记录了注册失败的详细信息
    except RegisterExistingUserError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": type(e).__name__, "detail": str(e)},
        )
    except ServiceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": type(e).__name__, "detail": str(e)},
        )


@router.get("/accounts/ClientRegister", status_code=status.HTTP_201_CREATED)
async def register_get(
    user_service: Annotated[UserService, Depends(get_user_service)],
    register_params: Annotated[RegisterParams, Query()],
) -> Response:
    """GET 方式，调用 _register 处理注册请求，返回认证令牌"""
    return await _register(register_params, user_service)


@router.post("/accounts/ClientRegister", status_code=status.HTTP_201_CREATED)
async def register_post(
    user_service: Annotated[UserService, Depends(get_user_service)],
    register_params: Annotated[RegisterParams, Form()],
) -> Response:
    """POST 方式，调用 _register 处理注册请求，返回认证令牌"""
    return await _register(register_params, user_service)


@router.get("/reader/api/0/token", status_code=status.HTTP_200_OK)
async def get_token(
    user: Annotated[User, Depends(get_current_user)],
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> Response:
    """获取 T Token"""
    auth = user_service.generate_auth(user)
    return Response(content=auth.split("/", 1)[1], media_type="text/plain")


@router.get("/reader/api/0/user-info", status_code=status.HTTP_200_OK)
async def get_user_info(
    user: Annotated[User, Depends(get_current_user)],
) -> dict:
    """获取用户信息"""
    return {
        "userId": user.username,
        "userName": user.friendly_name or user.username,
        "userProfileId": str(user.id),
        "userEmail": user.username,
        "isAdmin": user.is_admin,
    }


@router.delete("/accounts/DeleteAccount", status_code=status.HTTP_200_OK)
async def delete_account(
    username: str,
    user: Annotated[User, Depends(get_current_user)],
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> Response:
    """删除账户：自己删除自己或管理员删除其他用户"""
    if username != user.username:
        if not user.is_admin:
            _logger.warning(
                "非管理员用户 %s（%d）尝试删除其他用户 %s",
                user.username,
                user.id,
                username,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PermissionDenied",
                    "detail": "非管理员只能删除自己的账户",
                },
            )
        target = await user_service.get_by_username(username)
        if not target:
            _logger.warning(
                "管理员用户 %s（%d）尝试删除不存在的用户 %s",
                user.username,
                user.id,
                username,
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "UserNotFound", "detail": f"用户 '{username}' 不存在"},
            )
        target_user = target
    else:
        target_user = user

    try:
        await user_service.delete(target_user, user)
    except LastAdminDeletionError as e:
        # 此处不记日志，因为日志在 UserService.delete 中已经记录了删除失败的详细信息
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": type(e).__name__, "detail": str(e)},
        )
    return Response("OK", media_type="text/plain")
