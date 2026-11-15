"""修订号生成与状态流转辅助。"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models


async def create_revision(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    change_note: str,
    created_by: str = "system",
    source_doc_id: int | None = None,
) -> models.Revision:
    next_no = await db.scalar(
        select(func.coalesce(func.max(models.Revision.revision_no), 0) + 1).where(
            models.Revision.entity_type == entity_type,
            models.Revision.entity_id == entity_id,
        )
    )
    rev = models.Revision(
        entity_type=entity_type,
        entity_id=entity_id,
        revision_no=next_no,
        status=models.ST_DRAFT,
        change_note=change_note,
        created_by=created_by,
        source_doc_id=source_doc_id,
    )
    db.add(rev)
    await db.flush()
    return rev
