"""名册导入、续导、撤回与查询路由。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal

from pydantic import BaseModel

from ..auth import current_user, require_roles
from ..db import get_db
from .. import models
from ..schemas import ImportResultOut, RosterImportIn
from ..services.import_service import import_roster, repair_failed_row, resume_import

router = APIRouter(prefix="/api/rosters", tags=["rosters"])


class RowRepairIn(BaseModel):
    shares: Decimal
    votes_per_share: Decimal | None = None
    holder_name: str | None = None


@router.post("/import", response_model=ImportResultOut)
async def import_endpoint(
    body: RosterImportIn, db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("analyst")),
):
    result = await import_roster(db, body, user["username"])
    await db.commit()
    return result


@router.post("/resume/{source_ref}", response_model=ImportResultOut)
async def resume_endpoint(
    source_ref: str, db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("analyst")),
):
    result = await resume_import(db, source_ref)
    if result["document_id"] is None:
        raise HTTPException(status_code=404, detail=result["message"])
    await db.commit()
    return result


@router.post("/rows/{row_id}/repair")
async def repair_row(
    row_id: int, body: RowRepairIn,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_roles("analyst")),
):
    outcome = await repair_failed_row(
        db, row_id, body.shares, body.votes_per_share, body.holder_name,
        user["username"],
    )
    if not outcome["ok"]:
        raise HTTPException(status_code=409, detail=outcome["reason"])
    await db.commit()
    return outcome


@router.get("/documents")
async def list_documents(db: AsyncSession = Depends(get_db),
                         user: dict = Depends(current_user)):
    docs = list(await db.scalars(
        select(models.RosterDocument).order_by(models.RosterDocument.id.desc())
    ))
    out = []
    for d in docs:
        company = await db.get(models.Subject, d.company_subject_id)
        rev = await db.get(models.Revision, d.revision_id)
        out.append({
            "id": d.id,
            "source_ref": d.source_ref,
            "doc_title": d.doc_title,
            "company_subject_id": d.company_subject_id,
            "company_name": company.name if company else None,
            "effective_from": d.effective_from,
            "effective_to": d.effective_to,
            "process_status": d.process_status,
            "import_error": d.import_error,
            "total_rows": d.total_rows,
            "next_row_seq": d.next_row_seq,
            "corrected_by_doc_id": d.corrected_by_doc_id,
            "revision_id": d.revision_id,
            "revision_status": rev.status if rev else None,
        })
    return out


@router.get("/documents/{doc_id}/rows")
async def document_rows(doc_id: int, db: AsyncSession = Depends(get_db),
                        user: dict = Depends(current_user)):
    rows = await db.scalars(
        select(models.RosterRow).where(
            models.RosterRow.document_id == doc_id
        ).order_by(models.RosterRow.row_seq)
    )
    return [{
        "id": r.id,
        "row_seq": r.row_seq,
        "holder_subject_id": r.holder_subject_id,
        "holder_raw_name": r.holder_raw_name,
        "holder_type": r.holder_type,
        "share_class": r.share_class,
        "shares": str(r.shares),
        "votes_per_share": str(r.votes_per_share),
        "is_total": r.is_total,
        "registered_total": str(r.registered_total) if r.registered_total is not None else None,
        "effective_from": r.effective_from,
        "effective_to": r.effective_to,
        "provenance": r.provenance,
        "process_status": r.process_status,
        "error_detail": r.error_detail,
    } for r in rows]


@router.get("/import-events")
async def import_events(db: AsyncSession = Depends(get_db),
                        user: dict = Depends(current_user)):
    events = await db.scalars(
        select(models.ImportEvent).order_by(models.ImportEvent.id.desc()).limit(200)
    )
    return [{
        "id": e.id,
        "source_ref": e.source_ref,
        "content_fingerprint": e.content_fingerprint[:16] + "…",
        "event_kind": e.event_kind,
        "document_id": e.document_id,
        "row_offset": e.row_offset,
        "succeeded_rows": e.succeeded_rows,
        "failed_rows": e.failed_rows,
        "message": e.message,
        "created_at": e.created_at,
    } for e in events]
