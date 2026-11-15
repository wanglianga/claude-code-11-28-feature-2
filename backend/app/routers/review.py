"""修订审核、人工控制结论路由。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, require_roles
from ..config import settings
from ..db import get_db
from .. import models
from ..schemas import ManualControlIn, ManualControlOut, ReviewIn, RevisionOut
from ..services.review import review_document, review_generic
from ..services.revisions import create_revision

router = APIRouter(prefix="/api", tags=["review"])


@router.get("/revisions", response_model=list[RevisionOut])
async def list_revisions(db: AsyncSession = Depends(get_db),
                         user: dict = Depends(current_user)):
    return list(await db.scalars(
        select(models.Revision).order_by(models.Revision.id.desc()).limit(200)
    ))


@router.post("/revisions/{revision_id}/review", response_model=RevisionOut)
async def review_revision(
    revision_id: int, body: ReviewIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("reviewer")),
):
    revision = await db.get(models.Revision, revision_id)
    if not revision:
        raise HTTPException(status_code=404, detail="修订不存在")

    if revision.entity_type == models.REV_ROSTER_DOC:
        outcome = await review_document(
            db, revision, body.approve, user["username"], body.note,
            tolerance=settings.tolerance,
        )
    else:
        outcome = await review_generic(
            revision, body.approve, user["username"], body.note
        )
    if not outcome["ok"]:
        await db.commit()
        raise HTTPException(status_code=409, detail=outcome)
    await db.commit()
    await db.refresh(revision)
    return revision


@router.post("/manual-controls", response_model=ManualControlOut)
async def add_manual_control(
    body: ManualControlIn, db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("analyst")),
):
    company = await db.get(models.Subject, body.company_subject_id)
    controller = await db.get(models.Subject, body.controller_subject_id)
    if not company or not controller:
        raise HTTPException(status_code=404, detail="公司或控制人主体不存在")
    assertion = models.ManualControlAssertion(
        company_subject_id=body.company_subject_id,
        controller_subject_id=body.controller_subject_id,
        reason=body.reason, provenance=body.provenance,
        created_by=user["username"],
    )
    db.add(assertion)
    await db.flush()
    rev = await create_revision(
        db, models.REV_CONTROL, assertion.id,
        f"人工控制结论（章程约定）：{controller.name} 控制 {company.name}",
        user["username"],
    )
    assertion.revision_id = rev.id
    await db.commit()
    await db.refresh(assertion)
    return assertion


@router.get("/manual-controls", response_model=list[ManualControlOut])
async def list_manual_controls(db: AsyncSession = Depends(get_db),
                               user: dict = Depends(current_user)):
    return list(await db.scalars(
        select(models.ManualControlAssertion).order_by(
            models.ManualControlAssertion.id.desc())
    ))
