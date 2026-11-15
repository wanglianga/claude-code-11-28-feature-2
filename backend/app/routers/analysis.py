"""分析运行路由：创建、查询、发布。历史报告只读保留。"""
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, require_roles
from ..db import get_db
from .. import models
from ..schemas import AnalysisCreateIn, AnalysisRunOut, AnalysisRunSummary
from ..services.analysis import run_analysis

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.post("/runs")
async def create_run(
    body: AnalysisCreateIn, db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("analyst")),
):
    outcome = await run_analysis(db, body.as_of_date, user["username"])
    await db.commit()
    if not outcome["ok"]:
        raise HTTPException(status_code=409, detail=outcome)
    return outcome


@router.get("/runs", response_model=list[AnalysisRunSummary])
async def list_runs(db: AsyncSession = Depends(get_db),
                    user: dict = Depends(current_user)):
    return list(await db.scalars(
        select(models.AnalysisRun).order_by(models.AnalysisRun.id.desc()).limit(100)
    ))


@router.get("/runs/{run_id}", response_model=AnalysisRunOut)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db),
                  user: dict = Depends(current_user)):
    run = await db.get(models.AnalysisRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="分析运行不存在")

    docs = await db.scalars(
        select(models.AnalysisRunDoc).where(models.AnalysisRunDoc.run_id == run_id)
    )
    doc_list = []
    for d in docs:
        doc = await db.get(models.RosterDocument, d.document_id)
        company = await db.get(models.Subject, d.company_subject_id)
        doc_list.append({
            "document_id": d.document_id,
            "source_ref": doc.source_ref if doc else None,
            "doc_title": doc.doc_title if doc else None,
            "company_subject_id": d.company_subject_id,
            "company_name": company.name if company else None,
            "revision_id": d.revision_id,
            "effective_from": doc.effective_from if doc else None,
            "effective_to": doc.effective_to if doc else None,
            "fingerprint": doc.content_fingerprint[:16] + "…" if doc else None,
        })

    econ_rows = list(await db.scalars(
        select(models.EconomicInterest).where(
            models.EconomicInterest.run_id == run_id
        ).order_by(models.EconomicInterest.company_subject_id,
                    models.EconomicInterest.id)
    ))
    economic = []
    for e in econ_rows:
        company = await db.get(models.Subject, e.company_subject_id)
        detail = json.loads(e.detail_json or "{}")
        economic.append({
            "company_subject_id": e.company_subject_id,
            "company_name": company.name if company else str(e.company_subject_id),
            "holder_subject_id": e.holder_subject_id,
            "holder_label": e.holder_label,
            "is_unidentified": e.is_unidentified,
            "final_share": e.final_share,
            "solve_status": e.solve_status,
            "contributions": detail.get("contributions", []),
        })

    control_rows = list(await db.scalars(
        select(models.ControlFinding).where(
            models.ControlFinding.run_id == run_id
        ).order_by(models.ControlFinding.controlled_company_id)
    ))
    controls = []
    for c in control_rows:
        company = await db.get(models.Subject, c.controlled_company_id)
        detail = json.loads(c.detail_json or "{}")
        controls.append({
            "controlled_company_id": c.controlled_company_id,
            "company_name": company.name if company else str(c.controlled_company_id),
            "controller_subject_id": c.controller_subject_id,
            "controller_label": c.controller_label,
            "basis": c.basis,
            "controlled_votes": c.controlled_votes,
            "total_votes": c.total_votes,
            "vote_ratio": c.vote_ratio,
            "provenance": c.provenance,
            "expansion": detail.get("expansion", []),
        })

    return AnalysisRunOut(
        id=run.id, as_of_date=run.as_of_date, status=run.status,
        tolerance=run.tolerance,
        diagnostics=json.loads(run.diagnostics or "{}"),
        failure_reason=run.failure_reason,
        created_by=run.created_by, created_at=run.created_at,
        published_by=run.published_by, published_at=run.published_at,
        docs=doc_list, economic=economic, controls=controls,
    )


@router.post("/runs/{run_id}/publish", response_model=AnalysisRunSummary)
async def publish_run(run_id: int, db: AsyncSession = Depends(get_db),
                      user: dict = Depends(require_roles("reviewer"))):
    run = await db.get(models.AnalysisRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="分析运行不存在")
    if run.status == models.RUN_FAILED:
        raise HTTPException(status_code=409, detail="数值不稳定或求解失败的运行不得发布")
    if run.status == models.RUN_STALE:
        raise HTTPException(status_code=409, detail="结果已因更正名册过期，请重新计算")
    if run.status == models.RUN_PUBLISHED:
        raise HTTPException(status_code=409, detail="运行已发布")
    run.status = models.RUN_PUBLISHED
    run.published_by = user["username"]
    from datetime import datetime, timezone
    run.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(run)
    return run
