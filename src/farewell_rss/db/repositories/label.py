import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Label, LabelType

_logger = logging.getLogger(__name__)


class LabelRepository:
    """文件夹 / 标签仓库"""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, id_: int) -> Label | None:
        _logger.debug("获取标签 %d", id_)
        return await self._session.get(Label, id_)

    async def get_batch(self, ids: list[int]) -> dict[int, Label]:
        if not ids:
            return {}
        result = await self._session.execute(select(Label).where(Label.id.in_(ids)))
        labels = result.scalars().all()
        _logger.debug("批量获取 %d 个标签，获取到 %d 个", len(ids), len(labels))
        return {label.id: label for label in labels}

    async def get_by_user_name_type(
        self, user_id: int, name: str, type_: LabelType
    ) -> Label | None:
        _logger.debug(
            "按名称类型查找标签，用户: %d, 名称: %s, 类型: %s",
            user_id,
            name,
            type_.value,
        )
        return await self._session.scalar(
            select(Label).where(
                Label.user_id == user_id, Label.name == name, Label.type == type_
            )
        )

    async def list_by_user(self, user_id: int) -> list[Label]:
        result = await self._session.execute(
            select(Label).where(Label.user_id == user_id)
        )
        labels = result.scalars().all()
        _logger.debug("列出用户 %d 的标签，共 %d 个", user_id, len(labels))
        return list(labels)

    async def create(self, user_id: int, name: str, type_: LabelType) -> Label:
        label = Label(user_id=user_id, name=name, type=type_)
        self._session.add(label)
        await self._session.commit()
        _logger.debug("创建标签 %d，名称: %s，类型: %s", label.id, name, type_.value)
        return label

    async def update(self, label: Label, new_name: str) -> Label | None:
        existing = await self.get_by_user_name_type(label.user_id, new_name, label.type)
        if existing and existing.id != label.id:
            return None
        label.name = new_name
        await self._session.commit()
        _logger.debug("更新标签 %d，新名称: %s", label.id, new_name)
        return label

    async def delete(self, label: Label) -> None:
        _logger.debug("删除标签 %d", label.id)
        await self._session.delete(label)
        await self._session.commit()

    async def delete_by_user(self, user_id: int) -> None:
        _logger.debug("删除用户 %d 的所有标签", user_id)
        await self._session.execute(delete(Label).where(Label.user_id == user_id))
        await self._session.commit()
