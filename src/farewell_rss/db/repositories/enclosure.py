import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...feed_fetcher.feed_fetcher import FetchedEnclosure
from ..models import Enclosure
from ._chunking import chunked

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

    async def update_by_entries(
        self,
        enclosures: dict[int, list[FetchedEnclosure]],
    ) -> dict[int, list[Enclosure]]:
        """一次给多个条目对账附件（每个 value 是该条目**新的完整附件列表**）。

        逐条调 update_by_entry 会退化成「每条一次 SELECT」的 N+1：upsert 一个 50 条
        条目的源就多出 50 条查询。这里先按 chunked 分批、用一条 SELECT 把涉及到的
        条目的旧附件全取回来，再逐个在内存里 diff。

        传入 `{entry_id: []}` 表示清空该条目的附件（不是「不动」）。
        """
        if not enclosures:
            return {}

        old_by_entry: dict[int, list[Enclosure]] = {}
        for batch in chunked(list(enclosures)):
            rows = await self._session.scalars(
                select(Enclosure).where(Enclosure.entry_id.in_(batch))
            )
            for row in rows.all():
                old_by_entry.setdefault(row.entry_id, []).append(row)

        result: dict[int, list[Enclosure]] = {}
        for entry_id, new_enclosures in enclosures.items():
            old_enclosures = old_by_entry.get(entry_id, [])
            _logger.debug(
                "条目 %d 附件变更：%d → %d",
                entry_id,
                len(old_enclosures),
                len(new_enclosures),
            )
            result[entry_id] = await self._sync_by_entry(
                entry_id, old_enclosures, new_enclosures
            )

        await self._session.flush()

        return result

    async def update_by_entry(
        self, entry_id: int, new_enclosures: list[FetchedEnclosure]
    ) -> list[Enclosure]:
        """单条版本。一次要处理多条时直接用 update_by_entries，**别循环调这个**。"""
        result = await self.update_by_entries({entry_id: new_enclosures})
        return result[entry_id]

    async def _sync_by_entry(
        self,
        entry_id: int,
        old_enclosures: list[Enclosure],
        new_enclosures: list[FetchedEnclosure],
    ) -> list[Enclosure]:
        """把某个条目的附件从 old 对账到 new（两边按 href 排序后归并，与改之前同一套）"""
        old_enclosures.sort(key=lambda e: e.href)
        new_enclosures.sort(key=lambda e: e.href)
        result: list[Enclosure] = []

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
                result.append(self._add(entry_id, new))
                j += 1
        # 剩余的旧的需要删除
        while i < len(old_enclosures):
            await self._session.delete(old_enclosures[i])
            i += 1
        # 剩余的新的需要插入
        while j < len(new_enclosures):
            result.append(self._add(entry_id, new_enclosures[j]))
            j += 1

        return result

    def _add(self, entry_id: int, new: FetchedEnclosure) -> Enclosure:
        new_data = Enclosure(
            entry_id=entry_id,
            href=new.href,
            length=new.length,
            type=new.type,
        )
        self._session.add(new_data)
        return new_data

    async def delete_by_entry(self, entry_id: int) -> None:
        await self._session.execute(
            delete(Enclosure).where(Enclosure.entry_id == entry_id)
        )
        _logger.debug("删除条目 %d 的所有附件", entry_id)
        await self._session.flush()

    async def delete_by_entries(self, entry_ids: list[int]) -> None:
        """删除一批条目的全部附件。

        `IN (...)` 的参数个数有上限，分批的责任在**调用方**（见 entry.py 里
        MAX_IDS_PER_STATEMENT 的注释），这里不做二次分批。
        """
        if not entry_ids:
            _logger.debug("没有条目要删除附件")
            return
        await self._session.execute(
            delete(Enclosure).where(Enclosure.entry_id.in_(entry_ids))
        )
        # 只记条数：可能一次传进来上万个 id，整列打出来是一行几百 KB 的日志
        _logger.debug("删除 %d 个条目的所有附件", len(entry_ids))
        await self._session.flush()
