"""请求 / 响应 schema。"""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


# ---------- 认证 ----------
class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


# ---------- 主体 ----------
class SubjectIn(BaseModel):
    name: str
    subject_type: str  # company / person / institution
    unified_code: str | None = None


class SubjectOut(BaseModel):
    id: int
    name: str
    subject_type: str
    unified_code: str | None
    status: str
    merged_into_id: int | None
    revision_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- 合并候选 ----------
class MergeCandidateOut(BaseModel):
    id: int
    source_subject_id: int
    target_subject_id: int
    match_reason: str
    name_similarity: Decimal
    status: str
    conflict_note: str | None
    decided_by: str | None
    decided_at: datetime | None

    model_config = {"from_attributes": True}


class MergeDecisionIn(BaseModel):
    confirm: bool
    note: str | None = None


# ---------- 名册导入 ----------
class RosterRowIn(BaseModel):
    holder_name: str | None = None          # 持有人名称；总量行可空；"未知持有人"类名称保留为独立未知份额
    holder_unified_code: str | None = None
    holder_type: str = "company"            # company / person / institution
    share_class: str
    shares: Decimal
    votes_per_share: Decimal = Decimal("1")
    is_total: bool = False
    registered_total: Decimal | None = None
    effective_from: date
    effective_to: date | None = None
    provenance: str


class RosterImportIn(BaseModel):
    source_ref: str
    doc_title: str
    company_name: str
    company_unified_code: str | None = None
    effective_from: date
    effective_to: date | None = None
    # 更正时填写被更正来源标识
    corrects_source_ref: str | None = None
    withdrawal: bool = False
    rows: list[RosterRowIn] = Field(default_factory=list)


class ImportResultOut(BaseModel):
    event_kind: str
    document_id: int | None
    process_status: str | None
    total_rows: int
    succeeded_rows: int
    failed_rows: int
    next_row_seq: int
    duplicate: bool
    message: str
    failures: list[dict]


# ---------- 修订 / 审核 ----------
class RevisionOut(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    revision_no: int
    status: str
    change_note: str
    source_doc_id: int | None
    created_by: str
    reviewed_by: str | None
    created_at: datetime
    reviewed_at: datetime | None

    model_config = {"from_attributes": True}


class ReviewIn(BaseModel):
    approve: bool
    note: str | None = None


# ---------- 人工控制结论 ----------
class ManualControlIn(BaseModel):
    company_subject_id: int
    controller_subject_id: int
    reason: str
    provenance: str


class ManualControlOut(ManualControlIn):
    id: int
    active: bool
    revision_id: int
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- 分析 ----------
class AnalysisCreateIn(BaseModel):
    as_of_date: date


class EconomicOut(BaseModel):
    company_subject_id: int
    company_name: str
    holder_subject_id: int | None
    holder_label: str
    is_unidentified: bool
    final_share: Decimal
    solve_status: str
    contributions: list[dict]


class ControlOut(BaseModel):
    controlled_company_id: int
    company_name: str
    controller_subject_id: int | None
    controller_label: str
    basis: str
    controlled_votes: Decimal | None
    total_votes: Decimal | None
    vote_ratio: Decimal | None
    provenance: str | None
    expansion: list[dict]


class AnalysisRunOut(BaseModel):
    id: int
    as_of_date: date
    status: str
    tolerance: Decimal
    diagnostics: dict
    failure_reason: str | None
    created_by: str
    created_at: datetime
    published_by: str | None
    published_at: datetime | None
    docs: list[dict]
    economic: list[EconomicOut]
    controls: list[ControlOut]


class AnalysisRunSummary(BaseModel):
    id: int
    as_of_date: date
    status: str
    failure_reason: str | None
    created_at: datetime
    published_at: datetime | None

    model_config = {"from_attributes": True}
