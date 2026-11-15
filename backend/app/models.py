"""ORM 模型。

设计要点：
- 名册以"文档(roster_documents) -> 行(roster_rows)"两级存储，文档携带来源标识与内容指纹，
  支持幂等导入、更正与撤回；行级状态支持部分失败后从首个未处理行续导。
- 所有可审核数据都挂在 revision 上（明确修订），分析运行快照所采用的修订与文档。
- 主体合并不自动发生：同名只生成候选，统一社会标识不一致禁止归并，合并需人工确认且带冲突检查。
"""
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# 修订对象类型
REV_SUBJECT = "subject"
REV_ROSTER_DOC = "roster_document"
REV_ROSTER_ROW = "roster_row"
REV_MERGE = "subject_merge"
REV_CONTROL = "manual_control"

# 修订状态
ST_DRAFT = "draft"          # 已导入待审核
ST_REVIEW = "in_review"     # 审核中
ST_APPROVED = "approved"    # 已审核（分析只采用此状态）
ST_REJECTED = "rejected"
ST_SUPERSEDED = "superseded"  # 被更正名册替代
ST_WITHDRAWN = "withdrawn"    # 来源撤回

# 名册文档处理状态
DOC_RECEIVED = "received"
DOC_PROCESSING = "processing"
DOC_PARTIAL = "partial_failed"
DOC_IMPORTED = "imported"
DOC_REJECTED = "rejected"
DOC_WITHDRAWN = "withdrawn"

# 行处理状态
ROW_PENDING = "pending"
ROW_OK = "ok"
ROW_FAILED = "failed"

# 分析运行状态
RUN_DRAFT = "draft"          # 计算完成待发布
RUN_PUBLISHED = "published"
RUN_STALE = "stale"          # 计算后出现更正名册，待发布结果过期
RUN_FAILED = "failed"        # 数值不稳定等，未正式出数

# 主体类型
SUBJ_COMPANY = "company"
SUBJ_PERSON = "person"
SUBJ_INSTITUTION = "institution"


class Revision(Base):
    """明确修订：任何被分析采用的数据都能追溯到一条已审核修订。"""
    __tablename__ = "revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[int] = mapped_column(BigInteger, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default=ST_DRAFT, index=True)
    change_note: Mapped[str] = mapped_column(Text, default="")
    source_doc_id: Mapped[int | None] = mapped_column(
        ForeignKey("roster_documents.id", use_alter=True,
                   name="fk_revision_source_doc"),
        nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "revision_no", name="uq_revision"),
    )


class Subject(Base):
    """法律主体：企业 / 自然人 / 机构股东。"""
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    subject_type: Mapped[str] = mapped_column(String(16))  # company / person / institution
    # 统一社会标识（企业为统一社会信用代码；自然人/机构为证件/登记号）。未知为 NULL，禁止用空串冒充
    unified_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / merged
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id"), nullable=True)
    # 当前修订指针：实体先建、修订后挂，故可空
    revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("revisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    merged_into: Mapped["Subject | None"] = relationship(remote_side="Subject.id")


class MergeCandidate(Base):
    """同名匹配候选：系统只提议，归并必须人工确认。"""
    __tablename__ = "merge_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    target_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    match_reason: Mapped[str] = mapped_column(Text)          # 例如 exact_name / fuzzy_name
    name_similarity: Mapped[Numeric] = mapped_column(Numeric(6, 4), default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending / confirmed / rejected / conflict
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conflict_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_id: Mapped[int | None] = mapped_column(ForeignKey("revisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source_subject_id", "target_subject_id", name="uq_merge_pair"),
    )


class RosterDocument(Base):
    """一份股东名册（一个公司某时点的一套股本资料）。"""
    __tablename__ = "roster_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_ref: Mapped[str] = mapped_column(String(128), index=True)   # 来源记录标识
    content_fingerprint: Mapped[str] = mapped_column(String(128), index=True)  # SHA-256
    company_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    doc_title: Mapped[str] = mapped_column(String(256))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 更正指针：本文件被哪份新名册更正
    corrected_by_doc_id: Mapped[int | None] = mapped_column(
        ForeignKey("roster_documents.id"), nullable=True)
    process_status: Mapped[str] = mapped_column(String(24), default=DOC_RECEIVED, index=True)
    import_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 断点续导：下一个待处理行序号
    next_row_seq: Mapped[int] = mapped_column(Integer, default=0)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("revisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    rows: Mapped[list["RosterRow"]] = relationship(
        back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        # 同一来源记录标识 + 指纹唯一：重复导入直接命中
        UniqueConstraint("source_ref", "content_fingerprint", name="uq_source_fp"),
    )


class RosterRow(Base):
    """名册中的一条持有记录 / 股本类别登记总量记录。"""
    __tablename__ = "roster_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("roster_documents.id"), index=True)
    row_seq: Mapped[int] = mapped_column(Integer)
    holder_subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id"), nullable=True)
    # 未能识别为主体时保留原文名称，未知持有人独立成份额
    holder_raw_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # 持有人类型，供无代码行在续导阶段延迟建主体时使用
    holder_type: Mapped[str] = mapped_column(String(16), default="company")
    share_class: Mapped[str] = mapped_column(String(32))          # 股本类别，如 common / preferred
    shares: Mapped[Numeric] = mapped_column(Numeric(20, 4))       # 股份数
    votes_per_share: Mapped[Numeric] = mapped_column(Numeric(10, 4), default=1)  # 每股表决权
    # 该类别登记总量（仅 is_total=True 的行携带）；持有行与总量行分开
    is_total: Mapped[Boolean] = mapped_column(Boolean, default=False)
    registered_total: Mapped[Numeric | None] = mapped_column(Numeric(20, 4), nullable=True)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    provenance: Mapped[str] = mapped_column(Text)                 # 出处（页码/附件/文号）
    process_status: Mapped[str] = mapped_column(String(16), default=ROW_PENDING, index=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("revisions.id"))

    document: Mapped[RosterDocument] = relationship(back_populates="rows")

    __table_args__ = (
        UniqueConstraint("document_id", "row_seq", name="uq_doc_rowseq"),
        Index("ix_roster_row_holder", "holder_subject_id"),
    )


class AnalysisRun(Base):
    """一次分析运行：选定分析时点后，对当时有效且已审核名册的完整快照计算。"""
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default=RUN_DRAFT, index=True)
    tolerance: Mapped[Numeric] = mapped_column(Numeric(12, 10), default=1e-8)
    # 求解诊断摘要（JSON 文本）：残差、条件数、封闭公司、不稳定公司
    diagnostics: Mapped[str] = mapped_column(Text, default="{}")
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="analyst")
    published_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    docs: Mapped[list["AnalysisRunDoc"]] = relationship(cascade="all, delete-orphan")
    economic: Mapped[list["EconomicInterest"]] = relationship(cascade="all, delete-orphan")
    controls: Mapped[list["ControlFinding"]] = relationship(cascade="all, delete-orphan")


class AnalysisRunDoc(Base):
    """运行采用的名册快照（每个公司当时唯一一份有效已审核资料）。"""
    __tablename__ = "analysis_run_docs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("roster_documents.id"))
    company_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    revision_id: Mapped[int] = mapped_column(ForeignKey("revisions.id"))


class EconomicInterest(Base):
    """外部持有人对某公司的最终经济份额结果。"""
    __tablename__ = "economic_interests"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    company_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    holder_subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id"), nullable=True)
    holder_label: Mapped[str] = mapped_column(String(256))  # 未知持有人用 raw name
    is_unidentified: Mapped[Boolean] = mapped_column(Boolean, default=False)
    # 最终经济份额（占该公司全部股份的比例），未归一到已知部分
    final_share: Mapped[Numeric] = mapped_column(Numeric(18, 12))
    # 求解状态：unique（唯一解）/ non_unique（封闭不可唯一分配）
    solve_status: Mapped[str] = mapped_column(String(16))
    detail_json: Mapped[str] = mapped_column(Text, default="{}")  # 方程贡献明细


class ControlFinding(Base):
    """表决控制结论（与经济份额分别计算、分别存储）。"""
    __tablename__ = "control_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    controlled_company_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    controller_subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id"), nullable=True)
    controller_label: Mapped[str] = mapped_column(String(256))
    # direct（直接过半）/ expanded（累计受控公司票数后扩展）/ manual（章程人工附加）
    basis: Mapped[str] = mapped_column(String(16))
    # 人工章程结论无计算票数，故均可空
    controlled_votes: Mapped[Numeric | None] = mapped_column(Numeric(20, 4), nullable=True)
    total_votes: Mapped[Numeric | None] = mapped_column(Numeric(20, 4), nullable=True)
    vote_ratio: Mapped[Numeric | None] = mapped_column(Numeric(12, 8), nullable=True)
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)  # 人工结论出处
    detail_json: Mapped[str] = mapped_column(Text, default="{}")         # 控制扩展依据链


class ManualControlAssertion(Base):
    """章程另有约定：仅允许人工附加、带出处的控制结论；不修改经济份额。"""
    __tablename__ = "manual_control_assertions"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    controller_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"))
    reason: Mapped[str] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(Text)
    active: Mapped[Boolean] = mapped_column(Boolean, default=True)
    revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("revisions.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportEvent(Base):
    """导入事件日志：记录每次上传/续导的指纹判定与行级结果。"""
    __tablename__ = "import_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_ref: Mapped[str] = mapped_column(String(128), index=True)
    content_fingerprint: Mapped[str] = mapped_column(String(128))
    event_kind: Mapped[str] = mapped_column(String(24))  # new / duplicate / correction / withdrawal / resume
    document_id: Mapped[int | None] = mapped_column(ForeignKey("roster_documents.id"), nullable=True)
    row_offset: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_rows: Mapped[int] = mapped_column(Integer, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
