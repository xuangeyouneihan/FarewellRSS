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
    InvalidInviteCodeError,
    LastAdminDeletionError,
    ListUsersPermissionError,
    RegisterDisabledError,
    RegisterExistingUserError,
    ServiceError,
    UpdateAdminStatePermissionError,
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
    Passwd: str  # 密码（Google Reader/FreshRSS 标准字段）
    accountType: str = "GOOGLE"  # 固定
    service: str = "reader"  # 固定
    source: str = _get_source()  # RSS 服务标识


async def _sign_in(login_params: LoginParams, user_service: UserService) -> Response:
    """处理登录请求，返回认证令牌，GET 和 POST 的登录都会调用这个函数"""
    user = await user_service.authenticate(login_params.Email, login_params.Passwd)
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
    """注册参数，继承自 LoginParams，增加 friendly_name 和 invite_code"""

    friendly_name: str | None = None  # 昵称
    invite_code: str | None = None  # 邀请码（配置了 FAREWELL_RSS_INVITE_CODE 时必填）


async def _register(
    register_params: RegisterParams, user_service: UserService
) -> Response:
    """处理注册请求，返回认证令牌，GET 和 POST 的注册都会调用这个函数"""
    try:
        user = await user_service.register(
            register_params.Email,
            register_params.Passwd,
            register_params.friendly_name,
            register_params.invite_code,
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
    except RegisterDisabledError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": type(e).__name__, "detail": str(e)},
        )
    except InvalidInviteCodeError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
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
        "userName": user.friendly_name,
        "userProfileId": str(user.id),
        "userEmail": user.username,
        "isAdmin": user.is_admin,
    }


@router.get("/accounts/ListUsers", status_code=status.HTTP_200_OK)
async def list_users(
    user: Annotated[User, Depends(get_current_user)],
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> dict:
    """列出所有用户（仅管理员）"""
    try:
        users = await user_service.list_(user)
    except ListUsersPermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": type(e).__name__, "detail": str(e)},
        )
    return {
        "users": [
            {
                "username": u.username,
                "friendlyName": u.friendly_name,
                "isAdmin": u.is_admin,
            }
            for u in users
        ]
    }


@router.post("/accounts/CreateUser", status_code=status.HTTP_201_CREATED)
async def create_user(
    user_service: Annotated[UserService, Depends(get_user_service)],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    operator_username: Annotated[str, Form()],
    operator_password: Annotated[str, Form()],
    friendly_name: Annotated[str | None, Form()] = None,
    is_admin: Annotated[bool, Form()] = False,
) -> Response:
    """管理员创建用户，不受注册开关/邀请码限制"""
    if not password.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "InvalidPassword", "detail": "密码不能为空"},
        )
    operator = await _verify_operator(
        user_service, operator_username, operator_password
    )
    try:
        await user_service.create_user(
            operator,
            username,
            password,
            friendly_name=friendly_name,
            is_admin=is_admin,
        )
    except ListUsersPermissionError as e:
        _logger.warning(
            "非管理员用户 %s（%d）尝试创建用户 %s",
            operator.username,
            operator.id,
            username,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": type(e).__name__, "detail": "只有管理员可以创建用户"},
        )
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
    return Response("OK", media_type="text/plain", status_code=status.HTTP_201_CREATED)


async def _verify_operator(
    user_service: UserService,
    operator_username: str,
    operator_password: str,
) -> User:
    """验证操作者凭证；失败返回 400（避免触发前端 token 清理）"""
    operator = await user_service.authenticate(operator_username, operator_password)
    if not operator:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "InvalidCredentials", "detail": "用户名或密码错误"},
        )
    return operator


@router.post("/accounts/DeleteAccount", status_code=status.HTTP_200_OK)
async def delete_account(
    user_service: Annotated[UserService, Depends(get_user_service)],
    username: Annotated[str, Form()],
    operator_username: Annotated[str, Form()],
    operator_password: Annotated[str, Form()],
) -> Response:
    """删除账户：本人（需密码）或管理员（需其密码）删除其他用户"""
    operator = await _verify_operator(
        user_service, operator_username, operator_password
    )
    target = await user_service.get_by_username(username)
    if not target:
        _logger.warning(
            "操作者 %s 尝试删除不存在的用户 %s", operator.username, username
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "UserNotFound", "detail": f"用户 '{username}' 不存在"},
        )
    if operator.id != target.id and not operator.is_admin:
        _logger.warning(
            "非管理员用户 %s（%d）尝试删除其他用户 %s",
            operator.username,
            operator.id,
            username,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PermissionDenied", "detail": "非管理员只能删除自己的账户"},
        )
    try:
        await user_service.delete(target, operator)
    except LastAdminDeletionError as e:
        # 此处不记日志，因为日志在 UserService.delete 中已经记录了删除失败的详细信息
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": type(e).__name__, "detail": str(e)},
        )
    return Response("OK", media_type="text/plain")


@router.post("/accounts/EditProfile", status_code=status.HTTP_200_OK)
async def edit_profile(
    user: Annotated[User, Depends(get_current_user)],
    user_service: Annotated[UserService, Depends(get_user_service)],
    friendly_name: Annotated[str | None, Form()] = None,
) -> Response:
    """修改个人资料（当前仅限昵称）。缺省或空字符串都会将对应项置空。"""
    name = friendly_name.strip() if friendly_name is not None else None
    await user_service.update_profile(user, friendly_name=name)
    return Response("OK", media_type="text/plain")


@router.post("/accounts/ChangePassword", status_code=status.HTTP_200_OK)
async def change_password(
    user_service: Annotated[UserService, Depends(get_user_service)],
    username: Annotated[str, Form()],
    new_password: Annotated[str, Form()],
    operator_username: Annotated[str, Form()],
    operator_password: Annotated[str, Form()],
) -> Response:
    """修改密码：本人（旧密码）或管理员（其密码）修改其他用户"""
    if not new_password.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "InvalidNewPassword", "detail": "新密码不能为空"},
        )
    operator = await _verify_operator(
        user_service, operator_username, operator_password
    )
    target = await user_service.get_by_username(username)
    if not target:
        _logger.warning(
            "操作者 %s 尝试修改不存在用户 %s 的密码", operator.username, username
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "UserNotFound", "detail": f"用户 '{username}' 不存在"},
        )
    if operator.id != target.id and not operator.is_admin:
        _logger.warning(
            "非管理员用户 %s（%d）尝试修改他人密码", operator.username, operator.id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PermissionDenied", "detail": "非管理员只能修改自己的密码"},
        )
    await user_service.update_password(target, new_password)
    return Response("OK", media_type="text/plain")


@router.post("/accounts/SetAdmin", status_code=status.HTTP_200_OK)
async def set_admin(
    user_service: Annotated[UserService, Depends(get_user_service)],
    username: Annotated[str, Form()],
    is_admin: Annotated[bool, Form()],
    operator_username: Annotated[str, Form()],
    operator_password: Annotated[str, Form()],
) -> Response:
    """设置/取消管理员：仅管理员可操作（需其密码）。不允许取消最后一个管理员。"""
    operator = await _verify_operator(
        user_service, operator_username, operator_password
    )
    if not operator.is_admin:
        _logger.warning(
            "非管理员用户 %s（%d）尝试修改用户 %s 的管理员状态",
            operator.username,
            operator.id,
            username,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PermissionDenied",
                "detail": "只有管理员可以修改管理员状态",
            },
        )
    target = await user_service.get_by_username(username)
    if not target:
        _logger.warning(
            "操作者 %s 尝试修改不存在用户 %s 的管理员状态", operator.username, username
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "UserNotFound", "detail": f"用户 '{username}' 不存在"},
        )
    try:
        await user_service.update_admin_state(target, operator, is_admin)
    except UpdateAdminStatePermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": type(e).__name__, "detail": str(e)},
        )
    except LastAdminDeletionError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": type(e).__name__, "detail": str(e)},
        )
    return Response("OK", media_type="text/plain")
