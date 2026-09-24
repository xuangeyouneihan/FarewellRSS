import logging

from ..db.models import Label, LabelType, User
from ..db.repositories.label import LabelRepository
from .star_state import StarStateService
from .subscription import SubscriptionService

_logger = logging.getLogger(__name__)


class LabelService:
    """文件夹 / 标签服务"""

    def __init__(
        self,
        repository: LabelRepository,
        subscription_service: SubscriptionService,
        star_state_service: StarStateService,
    ):
        self._repository = repository
        self._subscription_service = subscription_service
        self._star_state_service = star_state_service

    async def get(self, id_: int) -> Label | None:
        return await self._repository.get(id_)

    async def get_batch(self, ids: list[int]) -> dict[int, Label]:
        return await self._repository.get_batch(ids)

    async def get_by_user_name_type(
        self, user: User, name: str, type_: LabelType
    ) -> Label | None:
        return await self._repository.get_by_user_name_type(user.id, name, type_)

    async def list_by_user(self, user: User) -> list[Label]:
        return await self._repository.list_by_user(user.id)

    async def create(self, user: User, name: str, type_: LabelType) -> Label:
        _logger.info(
            "用户 %s（%d）创建了 %s %s", user.username, user.id, type_.value, name
        )
        return await self._repository.create(user.id, name, type_)

    async def update(self, label: Label, new_name: str) -> Label | None:
        _logger.info(
            "更新 %s %s（%d）的名称为 %s",
            label.type.value,
            label.name,
            label.id,
            new_name,
        )
        return await self._repository.update(label, new_name)

    async def delete(self, label: Label) -> None:
        _logger.info("删除 %s %s（%d）", label.type.value, label.name, label.id)
        match label.type:
            case LabelType.FOLDER:
                await self._subscription_service.clear_folder(label)
            case LabelType.TAG:
                await self._star_state_service.clear_tag(label)
        await self._repository.delete(label)

    async def delete_by_user(self, user: User) -> None:
        await self._repository.delete_by_user(user.id)
