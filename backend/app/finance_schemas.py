"""财务事实层请求/响应 schema。"""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ProfitLineIn(BaseModel):
    line_no: int
    line_category: str = "operating"   # operating / dividend_income / other
    label: str
    amount: Decimal
    payer_company_subject_id: int | None = None
    share_class: str | None = None
    provenance: str = "虚构利润表"


class ProfitStatementIn(BaseModel):
    source_ref: str
    content_fingerprint: str | None = None
    company_subject_id: int
    period_code: str
    period_start: date
    period_end: date
    currency: str = "CNY"
    staging: str = "original"           # original / restated
    restates_source_ref: str | None = None
    provenance: str = "虚构利润表"
    lines: list[ProfitLineIn] = Field(default_factory=list)


class ProfitStatementOut(BaseModel):
    id: int
    company_subject_id: int
    company_name: str | None
    source_ref: str
    period_code: str
    period_start: date
    period_end: date
    currency: str
    status: str
    staging: str
    restates_statement_id: int | None
    provenance: str
    total_positive: Decimal | None = None
    lines: list[dict] = Field(default_factory=list)


class SnapshotHoldingIn(BaseModel):
    holder_subject_id: int | None = None
    holder_label: str
    share_class: str
    shares: Decimal
    class_total_shares: Decimal


class EquitySnapshotIn(BaseModel):
    company_subject_id: int
    snapshot_date: date
    source_ref: str
    status: str = "confirmable"
    provenance: str = "虚构股权快照"
    holdings: list[SnapshotHoldingIn] = Field(default_factory=list)


class ResolutionLineIn(BaseModel):
    line_no: int
    holder_subject_id: int | None = None
    holder_label: str
    share_class: str
    declared_amount: Decimal
    currency: str = "CNY"
    provenance: str = "虚构决议"


class DividendResolutionIn(BaseModel):
    company_subject_id: int
    resolution_no: str
    declaration_date: date
    record_date: date
    share_class: str
    total_declared_amount: Decimal
    currency: str = "CNY"
    funding_period_code: str | None = None
    provenance: str = "虚构董事会决议"
    lines: list[ResolutionLineIn] = Field(default_factory=list)


class ReceivableIn(BaseModel):
    issuer_subject_id: int
    collecting_subject_id: int
    receivable_no: str
    amount: Decimal
    currency: str = "CNY"
    confirmed_date: date
    resolution_id: int | None = None
    resolution_line_id: int | None = None
    source_ref: str
    provenance: str = "虚构应收通知"


class ReceiptIn(BaseModel):
    receipt_no: str
    collecting_subject_id: int
    payer_subject_id: int | None = None
    payer_label: str
    amount: Decimal
    currency: str
    direction: str = "inflow"
    value_date: date
    memo: str = ""
    source_ref: str
    provenance: str = "虚构银行回单"


class FxRateIn(BaseModel):
    from_currency: str
    to_currency: str
    rate: Decimal | None = None
    approved: bool = False
    source_ref: str
    provenance: str = "虚构汇率来源"
    effective_date: date | None = None


class ReconCreateIn(BaseModel):
    as_of_date: date


class ReconSummary(BaseModel):
    id: int
    as_of_date: date
    status: str
    created_by: str
    created_at: datetime
    published_at: datetime | None

    model_config = {"from_attributes": True}


class SealIn(BaseModel):
    note: str | None = None
