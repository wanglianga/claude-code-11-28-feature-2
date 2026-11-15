"""主体与同名匹配候选路由。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, require_roles
from ..db import get_db
from .. import models
from ..schemas import MergeCandidateOut, MergeDecisionIn, SubjectIn, SubjectOut
from ..services.revisions import create_revision
from ..services.subjects import decide_merge, resolve_or_create_holder, scan_fuzzy_candidates

router = APIRouter(prefix="/api", tags=["subjects"])


@router.get("/subjects", response_model=list[SubjectOut])
async def list_subjects(db: AsyncSession = Depends(get_db), user: dict = Depends(current_user)):
    result = await db.scalars(
        select(models.Subject).order_by(models.Subject.id)
    )
    return list(result)


@router.post("/subjects", response_model=SubjectOut)
async def create_subject(
    body: SubjectIn, db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("analyst")),
):
    if body.unified_code:
        existing = await db.scalar(
            select(models.Subject).where(
                models.Subject.unified_code == body.unified_code,
                models.Subject.status != "merged",
            )
        )
        if existing:
            raise HTTPException(status_code=409, detail="统一社会标识已存在，不得重复建档")
    subject = await resolve_or_create_holder(
        db, body.name, body.unified_code, body.subject_type, user["username"]
    )
    await db.commit()
    await db.refresh(subject)
    return subject


@router.get("/subjects/{subject_id}", response_model=SubjectOut)
async def get_subject(subject_id: int, db: AsyncSession = Depends(get_db),
                      user: dict = Depends(current_user)):
    subject = await db.get(models.Subject, subject_id)
    if not subject:
        raise HTTPException(status_code=404, detail="主体不存在")
    return subject


@router.get("/merge-candidates", response_model=list[MergeCandidateOut])
async def list_candidates(db: AsyncSession = Depends(get_db),
                          user: dict = Depends(current_user)):
    result = await db.scalars(
        select(models.MergeCandidate).order_by(models.MergeCandidate.id)
    )
    return list(result)


@router.post("/merge-candidates/scan")
async def scan_candidates(db: AsyncSession = Depends(get_db),
                          user: dict = Depends(require_roles("analyst"))):
    added = await scan_fuzzy_candidates(db)
    await db.commit()
    return {"new_candidates": added}


@router.post("/merge-candidates/{candidate_id}/decision", response_model=MergeCandidateOut)
async def candidate_decision(
    candidate_id: int, body: MergeDecisionIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("analyst")),
):
    # 行级锁：两人同时确认不同主体合并时串行化，依据关系修订在锁内核验，冲突退回人工
    candidate = await db.scalar(
        select(models.MergeCandidate)
        .where(models.MergeCandidate.id == candidate_id)
        .with_for_update()
    )
    if not candidate:
        raise HTTPException(status_code=404, detail="候选不存在")
    outcome = await decide_merge(db, candidate, body.confirm, user["username"], body.note)
    if not outcome["ok"]:
        await db.commit()
        raise HTTPException(status_code=409, detail=outcome["reason"])
    await db.commit()
    await db.refresh(candidate)
    return candidate
