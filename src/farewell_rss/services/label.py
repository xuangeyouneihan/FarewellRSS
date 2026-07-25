import logging
from datetime import datetime

from ..db.models import Entry, Label, LabelType, User
from ..db.repositories.label import LabelRepository
from .__init__ import Filtering
from .entry import EntryService
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
        entry_service: EntryService,
    ):
        self._repository = repository
        self._subscription_service = subscription_service
        self._star_state_service = star_state_service
        self._entry_service = entry_service

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

    async def list_entries(
        self,
        label: Label,
        start: datetime | None = None,
        end: datetime | None = None,
        include: Filtering | None = None,
        exclude: Filtering | None = None,
    ) -> list[Entry]:
        match label.type:
            case LabelType.FOLDER:
                subscriptions = await self._subscription_service.list_by_folder(label)
                entries: list[Entry] = []
                for subscription in subscriptions:
                    entries += await self._subscription_service.list_entries(
                        subscription, start, end, include, exclude
                    )
                _logger.debug(
                    "列出文件夹 %s（%d）下的条目，时间范围 %s 到 %s，包含 %s，排除 %s，共 %d 个",
                    label.name,
                    label.id,
                    start,
                    end,
                    include,
                    exclude,
                    len(entries),
                )
                return entries
            case LabelType.TAG:
                star_states = await self._star_state_service.list_by_tag(label.id)
                if not star_states:
                    return []
                entry_ids = [ss.entry_id for ss in star_states]
                raw = list((await self._entry_service.get_batch(entry_ids)).values())
                # 应用时间过滤
                if start is not None:
                    raw = [
                        e
                        for e in raw
                        if (e.published or e.updated or e.fetched)
                        and (e.published or e.updated or e.fetched) >= start
                    ]
                if end is not None:
                    raw = [
                        e
                        for e in raw
                        if (e.published or e.updated or e.fetched)
                        and (e.published or e.updated or e.fetched) <= end
                    ]
                # 应用 read/starred 过滤
                if include is Filtering.STARRED or (
                    include is None and exclude is not Filtering.STARRED
                ):
                    pass  # TAG 下的条目默认都是 starred
                _logger.debug(
                    "列出标签 %s（%d）下的条目，时间范围 %s 到 %s，包含 %s，排除 %s，共 %d 个",
                    label.name,
                    label.id,
                    start,
                    end,
                    include,
                    exclude,
                    len(raw),
                )
                return raw
