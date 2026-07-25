import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...feed_fetcher.feed_fetcher import FetchedEnclosure
from ..models import Enclosure

_logger = logging.getLogger(__name__)


class EnclosureRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_by_entry(self, entry_id: int) -> list[Enclosure]:
        raw_result = await self._session.execute(
            select(Enclosure).where(Enclosure.entry_id == entry_id)
        )
        result = list(raw_result.scalars().all())
        _logger.debug("查询条目 %d 的附件，共 %d 条", entry_id, len(result))
        return result

    async def update_by_entry(
        self, entry_id: int, new_enclosures: list[FetchedEnclosure], commit: bool = True
    ) -> list[Enclosure]:
        new_enclosures.sort(key=lambda e: e.href)
        temp = await self._session.execute(
            select(Enclosure).where(Enclosure.entry_id == entry_id)
        )
        old_enclosures = sorted(temp.scalars().all(), key=lambda e: e.href)

        _logger.debug(
            "条目 %d 附件变更：%d → %d",
            entry_id,
            len(old_enclosures),
            len(new_enclosures),
        )
        result = []

        i, j = 0, 0
        while i < len(old_enclosures) and j < len(new_enclosures):
            old = old_enclosures[i]
            new = new_enclosures[j]
            if old.href == new.href:
                # 更新
                old.length = new.length if new.length else old.length
                old.type = new.type if new.type else old.type
                i += 1
                j += 1
                result.append(old)
            elif old.href < new.href:
                # 删除旧的
                await self._session.delete(old)
                i += 1
            else:
                # 插入新的
                new_data = Enclosure(
                    entry_id=entry_id,
                    href=new.href,
                    length=new.length,
                    type=new.type,
                )
                self._session.add(new_data)
                result.append(new_data)
                j += 1
        # 剩余的旧的需要删除
        while i < len(old_enclosures):
            await self._session.delete(old_enclosures[i])
            i += 1
        # 剩余的新的需要插入
        while j < len(new_enclosures):
            new = new_enclosures[j]
            new_data = Enclosure(
                entry_id=entry_id,
                href=new.href,
                length=new.length,
                type=new.type,
            )
            self._session.add(new_data)
            result.append(new_data)
            j += 1

        if commit:
            await self._session.commit()
        else:
            await self._session.flush()

        return result

    async def delete_by_entry(self, entry_id: int) -> None:
        await self._session.execute(
            delete(Enclosure).where(Enclosure.entry_id == entry_id)
        )
        _logger.debug("删除条目 %d 的所有附件", entry_id)
