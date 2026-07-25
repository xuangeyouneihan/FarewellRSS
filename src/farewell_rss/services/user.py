import asyncio
import hashlib
import hmac
import logging
import os
from datetime import datetime

import bcrypt

from ..db.models import Entry, User
from ..db.repositories.user import UserRepository
from .__init__ import Filtering
from .exceptions import (
    LastAdminDeletionError,
    ListUsersPermissionError,
    RegisterExistingUserError,
    SlashInUsernameError,
    UpdateAdminStatePermissionError,
    UserDeletionPermissionError,
)
from .label import LabelService
from .read_state import ReadStateService
from .star_state import StarStateService
from .subscription import SubscriptionService

_logger = logging.getLogger(__name__)


class UserService:
    """用户业务逻辑 + 认证令牌"""

    def __init__(
        self,
        repository: UserRepository,
        subscription_service: SubscriptionService,
        label_service: LabelService,
        read_state_service: ReadStateService,
        star_state_service: StarStateService,
    ):
        self._repository = repository
        self._subscription_service = subscription_service
        self._label_service = label_service
        self._read_state_service = read_state_service
        self._star_state_service = star_state_service

    async def get(self, id_: int) -> User | None:
        return await self._repository.get(id_)

    async def register(
        self, username: str, password: str, friendly_name: str | None = None
    ) -> User:
        if "/" in username:
            raise SlashInUsernameError("用户名中不允许包含斜杠（/）")
        existing = await self._repository.get_by_username(username)
        if existing:
            raise RegisterExistingUserError.from_username(username)
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        users = await self._repository.list_()
        is_admin = len(users) == 0  # 第一个注册的用户自动成为管理员
        _logger.info(
            "注册新用户 %s，昵称：%s，是否管理员：%s",
            username,
            friendly_name,
            is_admin,
        )
        return await self._repository.register(
            username=username,
            password_hash=password_hash,
            friendly_name=friendly_name,
            is_admin=is_admin,
        )

    async def authenticate(self, username: str, password: str) -> User | None:
        user = await self._repository.get_by_username(username)
        if (
            user
            and bcrypt.checkpw(password.encode(), user.password_hash.encode())
            and not user.deleted_at
        ):
            _logger.info("用户 %s（%d）认证成功", username, user.id)
            return user
        if user:
            _logger.warning("用户 %s（%d）认证失败", username, user.id)
        else:
            _logger.warning("用户 %s 认证失败，用户不存在", username)
        return None

    async def list_(self, admin: User) -> list[User]:
        if not admin.is_admin:
            raise ListUsersPermissionError
        return await self._repository.list_()

    async def update_username(self, user: User, new_username: str) -> User | None:
        _logger.info("更新用户 %d 的用户名为 %s", user.id, new_username)
        return await self._repository.update_username(user, new_username)

    async def update_password(self, user: User, new_password: str) -> User | None:
        _logger.info("更新用户 %d 的密码", user.id)
        new_password_hash = bcrypt.hashpw(
            new_password.encode(), bcrypt.gensalt()
        ).decode()
        return await self._repository.update_password(user, new_password_hash)

    async def update_profile(
        self, user: User, *, friendly_name: str | None
    ) -> User | None:
        _logger.info("更新用户 %d 的个人资料", user.id)
        return await self._repository.update_profile(user, friendly_name=friendly_name)

    async def update_admin_state(
        self, user: User, operator: User, is_admin: bool
    ) -> User | None:
        if not operator.is_admin:
            raise UpdateAdminStatePermissionError
        if len(await self._repository.list_admins()) == 1 and not is_admin:
            # 不允许删除最后一个管理员
            raise LastAdminDeletionError.from_username(user.username)
        _logger.info(
            "用户 %d 更新用户 %d 的管理员状态为 %s",
            operator.id,
            user.id,
            is_admin,
        )
        return await self._repository.update_admin_state(user, is_admin)

    async def delete(self, user: User, operator: User) -> None:
        if not operator.is_admin and operator.id != user.id:
            raise UserDeletionPermissionError
        if len(await self._repository.list_admins()) == 1 and user.is_admin:
            # 不允许删除最后一个管理员
            raise LastAdminDeletionError.from_username(user.username)
        _logger.info("用户 %d 将用户 %d 标记为已删除", operator.id, user.id)
        await self._repository.mark_as_deleted(user)
        asyncio.create_task(self._hard_delete(user))

    async def list_entries(
        self,
        user: User,
        start: datetime | None = None,
        end: datetime | None = None,
        include: Filtering | None = None,
        exclude: Filtering | None = None,
    ) -> list[Entry]:
        """列出用户所有订阅的文章，支持过滤"""
        subscriptions = await self._subscription_service.list_by_user(user)
        entries: list[Entry] = []
        for subscription in subscriptions:
            subscription_entries = await self._subscription_service.list_entries(
                subscription, start, end, include, exclude
            )
            entries += subscription_entries
        return entries

    def generate_auth(self, user: User) -> str:
        """为用户生成 Google Reader API 的 Auth/SID token"""
        signature = hmac.new(
            self._secret().encode(),
            user.username.encode(),
            hashlib.sha256,
        ).hexdigest()
        _logger.info("为用户 %d 生成 Auth token", user.id)
        return f"{user.username}/{signature}"

    async def verify_auth(self, token: str) -> User | None:
        """验证 Auth token，成功返回对应的 User"""
        try:
            username, signature = token.split("/", 1)
        except ValueError:
            return None
        expected = hmac.new(
            self._secret().encode(),
            username.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            _logger.warning("Auth token 验证失败，签名不匹配")
            return None
        _logger.info("Auth token 验证成功，用户名：%s", username)
        return await self._repository.get_by_username(username)

    async def _hard_delete(self, user: User) -> None:
        _logger.info("彻底清理用户 %d 的数据", user.id)
        await self._read_state_service.delete_by_user(user)
        await self._star_state_service.delete_by_user(user)
        await self._subscription_service.delete_by_user(user)
        await self._label_service.delete_by_user(user)
        await self._repository.delete(user)

    @staticmethod
    def _secret() -> str:
        return os.getenv("FAREWELL_RSS_SECRET", "")
