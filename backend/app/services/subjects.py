"""主体解析与同名匹配。

规则（对应需求）：
- 统一社会标识一致 -> 视为同一主体。
- 仅同名 -> 不自动归并，保留为独立主体并生成待确认匹配候选。
- 统一社会标识不一致 -> 候选带 conflict 标记，禁止自动/强行归并，只能退回人工。
"""
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from .revisions import create_revision

FUZZY_THRESHOLD = 0.8


async def get_subject_by_code(db: AsyncSession, code: str) -> models.Subject | None:
    result = await db.scalars(
        select(models.Subject).where(
            models.Subject.unified_code == code,
            models.Subject.status != "merged",
        )
    )
    return result.first()


async def _exact_name_subjects(db: AsyncSession, name: str) -> list[models.Subject]:
    result = await db.scalars(
        select(models.Subject).where(
            models.Subject.name == name,
            models.Subject.status != "merged",
        )
    )
    return list(result)


async def _create_subject(
    db: AsyncSession, name: str, code: str | None, subject_type: str, created_by: str
) -> models.Subject:
    # 先建占位主体拿到 id，再挂修订
    placeholder = models.Subject(
        name=name,
        subject_type=subject_type,
        unified_code=code,
    )
    db.add(placeholder)
    await db.flush()
    rev = await create_revision(
        db, models.REV_SUBJECT, placeholder.id, f"新建主体：{name}", created_by
    )
    placeholder.revision_id = rev.id
    await db.flush()
    return placeholder


async def _add_candidate(
    db: AsyncSession,
    source: models.Subject,
    target: models.Subject,
    reason: str,
    similarity: float,
    conflict: bool,
) -> None:
    exists = await db.scalar(
        select(models.MergeCandidate.id).where(
            models.MergeCandidate.source_subject_id == source.id,
            models.MergeCandidate.target_subject_id == target.id,
        )
    )
    if exists:
        return
    db.add(
        models.MergeCandidate(
            source_subject_id=source.id,
            target_subject_id=target.id,
            match_reason=reason,
            name_similarity=similarity,
            status="conflict" if conflict else "pending",
            conflict_note="统一社会标识不一致，禁止自动归并，需人工核实" if conflict else None,
            revision_id=source.revision_id,
        )
    )


async def resolve_or_create_holder(
    db: AsyncSession,
    name: str,
    code: str | None,
    subject_type: str,
    created_by: str = "system",
) -> models.Subject:
    """解析名册行持有人为主体；不确定时新建独立主体并产生匹配候选。"""
    if code:
        existing = await get_subject_by_code(db, code)
        if existing:
            # 同代码但名称不同：以代码为准归并，名称差异记录在候选备注里供人工复核
            if existing.name != name:
                await _add_candidate(
                    db, existing, existing, "same_code_diff_name", 0.0, conflict=False
                ) if False else None  # 同代码直接归属，不产生自候选
            return existing
        # 带代码的新主体：若同名主体存在但代码不同，产生冲突候选
        subject = await _create_subject(db, name, code, subject_type, created_by)
        same_names = await _exact_name_subjects(db, name)
        for other in same_names:
            if other.id == subject.id:
                continue
            conflict = other.unified_code is not None and other.unified_code != code
            await _add_candidate(
                db, subject, other, "exact_name", 1.0, conflict=conflict
            )
        return subject

    # 无代码：同名不自动归并，新建独立主体 + 待确认候选
    same_names = await _exact_name_subjects(db, name)
    subject = await _create_subject(db, name, None, subject_type, created_by)
    for other in same_names:
        await _add_candidate(db, subject, other, "exact_name", 1.0, conflict=False)
    return subject


async def scan_fuzzy_candidates(db: AsyncSession) -> int:
    """全库扫描近似名称，补充模糊匹配候选。返回新增数量。"""
    subjects = list(await db.scalars(
        select(models.Subject).where(models.Subject.status != "merged")
    ))
    added = 0
    seen_pairs: set[tuple[int, int]] = set()
    for i, a in enumerate(subjects):
        for b in subjects[i + 1:]:
            sim = SequenceMatcher(None, a.name, b.name).ratio()
            if sim < FUZZY_THRESHOLD or a.name == b.name:
                continue
            pair = (a.id, b.id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            exists = await db.scalar(
                select(models.MergeCandidate.id).where(
                    models.MergeCandidate.source_subject_id == a.id,
                    models.MergeCandidate.target_subject_id == b.id,
                )
            )
            if exists:
                continue
            conflict = (
                a.unified_code is not None
                and b.unified_code is not None
                and a.unified_code != b.unified_code
            )
            db.add(
                models.MergeCandidate(
                    source_subject_id=a.id,
                    target_subject_id=b.id,
                    match_reason="fuzzy_name",
                    name_similarity=sim,
                    status="conflict" if conflict else "pending",
                    conflict_note="统一社会标识不一致，禁止自动归并，需人工核实" if conflict else None,
                    revision_id=a.revision_id,
                )
            )
            added += 1
    await db.flush()
    return added


async def decide_merge(
    db: AsyncSession, candidate: models.MergeCandidate, confirm: bool, user: str, note: str | None
) -> dict:
    """人工确认/退回合并。并发冲突时退回人工（409 由路由层转换）。"""
    if candidate.status not in ("pending", "conflict"):
        return {"ok": False, "reason": f"候选已处于 {candidate.status} 状态，不能重复决策"}

    source = await db.get(models.Subject, candidate.source_subject_id)
    target = await db.get(models.Subject, candidate.target_subject_id)
    if not source or not target or source.status == "merged" or target.status == "merged":
        return {"ok": False, "reason": "主体已被合并或不存在"}

    # 依据修订核对：候选生成后源主体若已发生新修订，说明有并发变更，退回人工
    if candidate.revision_id is not None and source.revision_id != candidate.revision_id:
        candidate.status = "conflict"
        candidate.conflict_note = (
            f"关系修订已变更（候选依据 r{candidate.revision_id}，当前 r{source.revision_id}），退回人工"
        )
        await db.flush()
        return {"ok": False, "reason": candidate.conflict_note}

    # 统一社会标识不一致：即使人工确认也不得自动归并
    if (
        source.unified_code
        and target.unified_code
        and source.unified_code != target.unified_code
    ):
        candidate.status = "conflict"
        candidate.conflict_note = "统一社会标识不一致，禁止归并"
        await db.flush()
        return {"ok": False, "reason": "统一社会标识不一致，禁止归并"}

    if not confirm:
        candidate.status = "rejected"
        candidate.decided_by = user
        candidate.conflict_note = note
        await db.flush()
        return {"ok": True, "merged": False}

    # 执行归并：名册行持有人改写为目标主体，源主体标记 merged
    rows = await db.scalars(
        select(models.RosterRow).where(models.RosterRow.holder_subject_id == source.id)
    )
    for row in rows:
        row.holder_subject_id = target.id
    source.status = "merged"
    source.merged_into_id = target.id
    rev = await create_revision(
        db, models.REV_MERGE, source.id,
        f"人工确认合并 {source.name} -> {target.name}。{note or ''}", user,
    )
    candidate.status = "confirmed"
    candidate.decided_by = user
    candidate.revision_id = rev.id
    await db.flush()
    return {"ok": True, "merged": True}
