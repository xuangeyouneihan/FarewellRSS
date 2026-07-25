import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User

_logger = logging.getLogger(__name__)


class UserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def register(
        self,
        username: str,
        password_hash: str,
        friendly_name: str | None = None,
        is_admin: bool = False,
    ) -> User:
        user = User(
            username=username,
            password_hash=password_hash,
            friendly_name=friendly_name,
            is_admin=is_admin,
        )
        self._session.add(user)
        await self._session.commit()
        _logger.debug("注册用户 %d，用户名: %s", user.id, username)
        return user

    async def get(self, id_: int) -> User | None:
        _logger.debug("获取用户 %d", id_)
        return await self._session.get(User, id_)

    async def get_by_username(self, username: str) -> User | None:
        _logger.debug("按用户名查找: %s", username)
        return await self._session.scalar(select(User).where(User.username == username))

    async def list_(self) -> list[User]:
        _logger.debug("列出所有用户")
        result = await self._session.execute(select(User))
        return list(result.scalars().all())

    async def list_admins(self) -> list[User]:
        _logger.debug("列出所有管理员")
        result = await self._session.execute(select(User).where(User.is_admin))
        return list(result.scalars().all())

    async def update_username(self, user: User, new_username: str) -> User | None:
        _logger.debug(
            "更新用户名 %d: '%s' -> '%s'", user.id, user.username, new_username
        )
        existing = await self.get_by_username(new_username)
        if existing and existing.id != user.id:
            _logger.debug(
                "用户名 %s 已存在，无法更新用户 %d 的用户名", new_username, user.id
            )
            return None
        user.username = new_username
        await self._session.commit()
        return user

    async def update_password(self, user: User, new_password_hash: str) -> User | None:
        _logger.debug("更新用户 %d 的密码", user.id)
        user.password_hash = new_password_hash
        await self._session.commit()
        return user

    async def update_profile(
        self, user: User, *, friendly_name: str | None
    ) -> User | None:
        _logger.debug("更新用户 %d 的昵称: '%s'", user.id, friendly_name)
        user.friendly_name = friendly_name
        await self._session.commit()
        return user

    async def update_admin_state(self, user: User, is_admin: bool) -> User | None:
        _logger.debug("更新用户 %d 的管理员状态: %s", user.id, is_admin)
        user.is_admin = is_admin
        await self._session.commit()
        return user

    async def mark_as_deleted(self, user: User) -> None:
        _logger.debug("标记用户 %d 为已删除", user.id)
        user.deleted_at = datetime.now(datetime.UTC)
        await self._session.commit()

    async def delete(self, user: User) -> None:
        _logger.debug("永久删除用户 %d", user.id)
        await self._session.delete(user)
        await self._session.commit()
