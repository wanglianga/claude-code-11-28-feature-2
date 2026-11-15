"""ORM 模型。

设计要点：
- 名册以"文档(roster_documents) -> 行(roster_rows)"两级存储，文档携带来源标识与内容指纹，
  支持幂等导入、更正与撤回；行级状态支持部分失败后从首个未处理行续导。
- 所有可审核数据都挂在 revision 上（明确修订），分析运行快照所采用的修订与文档。
- 主体合并不自动发生：同名只生成候选，统一社会标识不一致禁止归并，合并需人工确认且带冲突检查。
"""
from datetime import date, datetime
from decimal import Decimal

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
REV_PROFIT = "profit_statement"
REV_RESOLUTION = "dividend_resolution"

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


# =====================================================================
# 利润来源穿透与跨期对账（财务事实层）
#
# 纪律（对应需求）：
# - 利润表按会计期间、决议按记录日、应收按确认日、回单按到账日分别建模，
#   绝不把"同月发生"当作同一会计事实。
# - 来源分配只采用决议记录日的可确认股权快照；缺失时列"权属待证"，不借用最近一期。
# - 交叉持股保留路径贡献与残差，循环部分不重复累加。
# - 回单一对多且摘要无法区分时保持候选并从已核验到账中排除。
# - 重述只在跨期桥接中生成差异，不回写已封存审计快照。
# - 无获批汇率来源时只发布原币金额与缺失汇率提示，不按市场汇率自行换算。
# - 不执行分红批准、现金预留、银行付款、退款或余额扣还。
# =====================================================================

# 利润表 / 决议 / 应收 状态
FIN_DRAFT = "draft"
FIN_APPROVED = "approved"
FIN_SUPERSEDED = "superseded"   # 利润表被更正/重述后保留原值
FIN_RESTATED = "restated"       # 重述后的新利润表

# 股权快照状态
SNAP_CONFIRMABLE = "confirmable"   # 记录日可确认的时点快照
SNAP_UNCONFIRMED = "unconfirmed"   # 仅登记、不可用于来源分配

# 回单匹配结论
MATCH_VERIFIED = "verified"           # 唯一匹配，计入已核验到账
MATCH_ONE_TO_MANY = "one_to_many"     # 一对多等额冲突，保持候选、排除出已核验
MATCH_SPLIT = "split"                 # 一张应收由多张回单拆分，理由记为事实
MATCH_AGGREGATED = "aggregated"       # 一张回单汇聚多张应收，理由记为事实
MATCH_CANDIDATE = "candidate"         # 弱证据候选，不核验
MATCH_EXCLUDED = "excluded"           # 明确排除

# 跨期桥接层
BR_DECLARED = "declared_receivable"     # 已声明应收
BR_VERIFIED = "verified_receipt"        # 可核验到账
BR_FX = "fx_difference"                 # 汇率差异
BR_RESTATE = "restatement_impact"       # 重述影响
BR_UNALLOC = "unallocatable"            # 无法归属

# 对账运行状态
RECON_DRAFT = "draft"
RECON_PUBLISHED = "published"
RECON_SEALED = "sealed"                 # 审计快照已封存，只读


class ProfitStatement(Base):
    """某公司某会计期间的利润表（可被重述/更正，但原表不删除）。"""
    __tablename__ = "profit_statements"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    source_ref: Mapped[str] = mapped_column(String(128), index=True)
    content_fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    period_code: Mapped[str] = mapped_column(String(32), index=True)   # 如 2025-FY / 2026-H1
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    status: Mapped[str] = mapped_column(String(16), default=FIN_DRAFT, index=True)
    # 重述指针：新表通过该字段指向被重述的旧表；旧表保留原值，标记 superseded
    restates_statement_id: Mapped[int | None] = mapped_column(
        ForeignKey("profit_statements.id"), nullable=True)
    staging: Mapped[str] = mapped_column(String(16), default="original")  # original / restated
    provenance: Mapped[str] = mapped_column(Text)
    revision_id: Mapped[int | None] = mapped_column(ForeignKey("revisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["ProfitLine"]] = relationship(
        back_populates="statement", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source_ref", "content_fingerprint", name="uq_profit_source_fp"),
    )


class ProfitLine(Base):
    """利润表中的一行：经营或投资收益行；投资分红行可指向付款公司以便穿透。"""
    __tablename__ = "profit_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("profit_statements.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    line_category: Mapped[str] = mapped_column(String(24))  # operating / dividend_income / other
    label: Mapped[str] = mapped_column(String(256))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    # 分红收益行的付款公司：用于向下穿透到该公司的利润来源
    payer_company_subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id"), nullable=True)
    share_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provenance: Mapped[str] = mapped_column(Text)

    statement: Mapped[ProfitStatement] = relationship(back_populates="lines")

    __table_args__ = (UniqueConstraint("statement_id", "line_no", name="uq_profit_line_no"),)


class EquitySnapshot(Base):
    """时点股权快照：来源分配只允许采用记录日可确认的快照。"""
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)  # 记录日/时点
    source_ref: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(16), default=SNAP_CONFIRMABLE, index=True)
    analysis_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_runs.id"), nullable=True)
    provenance: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    holdings: Mapped[list["EquitySnapshotHolding"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("company_subject_id", "snapshot_date", "source_ref",
                         name="uq_snapshot_company_date_ref"),
    )


class EquitySnapshotHolding(Base):
    """快照中的一条持股：按股份类别记录股份数与占比。"""
    __tablename__ = "equity_snapshot_holdings"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("equity_snapshots.id"), index=True)
    holder_subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id"), nullable=True)
    holder_label: Mapped[str] = mapped_column(String(256))
    share_class: Mapped[str] = mapped_column(String(32), index=True)
    shares: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    class_total_shares: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    ratio: Mapped[Decimal] = mapped_column(Numeric(18, 8))

    snapshot: Mapped[EquitySnapshot] = relationship(back_populates="holdings")

    __table_args__ = (Index("ix_snapshot_holding_holder", "holder_subject_id"),)


class DividendResolution(Base):
    """董事会分红决议：记录日用于确定有权股权，与会计期间、到账日相互独立。"""
    __tablename__ = "dividend_resolutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    resolution_no: Mapped[str] = mapped_column(String(64), index=True)
    declaration_date: Mapped[date] = mapped_column(Date)   # 决议记录日
    record_date: Mapped[date] = mapped_column(Date, index=True)  # 股权登记日
    share_class: Mapped[str] = mapped_column(String(32))
    total_declared_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    # 决议所分配的利润来自哪个会计期间（与记录日、确认日、到账日无关）
    funding_period_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=FIN_DRAFT, index=True)
    provenance: Mapped[str] = mapped_column(Text)
    revision_id: Mapped[int | None] = mapped_column(ForeignKey("revisions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["ResolutionLine"]] = relationship(
        back_populates="resolution", cascade="all, delete-orphan")


class ResolutionLine(Base):
    """决议中分配给某上层持有人的一行；应收即由此产生。"""
    __tablename__ = "resolution_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    resolution_id: Mapped[int] = mapped_column(ForeignKey("dividend_resolutions.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    holder_subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id"), nullable=True)
    holder_label: Mapped[str] = mapped_column(String(256))
    share_class: Mapped[str] = mapped_column(String(32))
    declared_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    provenance: Mapped[str] = mapped_column(Text)

    resolution: Mapped[DividendResolution] = relationship(back_populates="lines")

    __table_args__ = (UniqueConstraint("resolution_id", "line_no", name="uq_resolution_line_no"),)


class ReceivableNotice(Base):
    """子公司应收通知：按收款主体与应收确认日记录。"""
    __tablename__ = "receivable_notices"

    id: Mapped[int] = mapped_column(primary_key=True)
    issuer_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)   # 出具通知的子公司
    collecting_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)  # 收款主体/上层公司
    receivable_no: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    confirmed_date: Mapped[date] = mapped_column(Date, index=True)   # 应收确认日
    resolution_id: Mapped[int | None] = mapped_column(
        ForeignKey("dividend_resolutions.id"), nullable=True)
    resolution_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("resolution_lines.id"), nullable=True)
    source_ref: Mapped[str] = mapped_column(String(128), index=True)
    provenance: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=FIN_APPROVED, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BankReceipt(Base):
    """银行回单：按到账日期记录；外部回单号、币种、方向、付款主体、摘要用于匹配。"""
    __tablename__ = "bank_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_no: Mapped[str] = mapped_column(String(64), index=True)   # 外部回单号
    collecting_subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    payer_subject_id: Mapped[int | None] = mapped_column(ForeignKey("subjects.id"), nullable=True)
    payer_label: Mapped[str] = mapped_column(String(256))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(8))
    direction: Mapped[str] = mapped_column(String(8), default="inflow")  # 仅入账回单可核验应收
    value_date: Mapped[date] = mapped_column(Date, index=True)           # 到账日期
    memo: Mapped[str] = mapped_column(Text, default="")
    source_ref: Mapped[str] = mapped_column(String(128), index=True)
    provenance: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FxRate(Base):
    """获批汇率来源：只有 approved 且未作废的汇率可用于换算。"""
    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_currency: Mapped[str] = mapped_column(String(8), index=True)
    to_currency: Mapped[str] = mapped_column(String(8))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    source_ref: Mapped[str] = mapped_column(String(128), index=True)
    provenance: Mapped[str] = mapped_column(Text)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("from_currency", "to_currency", "source_ref", name="uq_fx_ref"),
    )


class ReconciliationRun(Base):
    """一次跨期对账运行：在分析日编译来源链、匹配回单、分层桥接。"""
    __tablename__ = "reconciliation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default=RECON_DRAFT, index=True)
    diagnostics: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(64), default="analyst")
    published_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chains: Mapped[list["SourceChain"]] = relationship(cascade="all, delete-orphan")
    matches: Mapped[list["ReceiptMatch"]] = relationship(cascade="all, delete-orphan")
    bridges: Mapped[list["BridgeLayer"]] = relationship(cascade="all, delete-orphan")


class SourceChain(Base):
    """来源链：从应收金额经决议行、股权快照、利润表行到经营公司；含残差。"""
    __tablename__ = "source_chains"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_runs.id"), index=True)
    receivable_id: Mapped[int] = mapped_column(ForeignKey("receivable_notices.id"), index=True)
    entry_kind: Mapped[str] = mapped_column(String(24))  # operating_line / cycle_residual / title_pending / unallocatable
    path_json: Mapped[str] = mapped_column(Text, default="[]")
    terminal_company_subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id"), nullable=True)
    terminal_statement_id: Mapped[int | None] = mapped_column(
        ForeignKey("profit_statements.id"), nullable=True)
    terminal_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("profit_lines.id"), nullable=True)
    path_weight: Mapped[Decimal] = mapped_column(Numeric(18, 10))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))       # 该链贡献金额（原币）
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")


class ReceiptMatch(Base):
    """回单与应收的匹配：一对一、拆分、汇聚或一对多冲突均记录规则与理由。"""
    __tablename__ = "receipt_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_runs.id"), index=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("bank_receipts.id"), index=True)
    receivable_id: Mapped[int | None] = mapped_column(
        ForeignKey("receivable_notices.id"), nullable=True)
    rule: Mapped[str] = mapped_column(String(32))  # external_ref / amount_payer / memo / split / aggregate / near
    status: Mapped[str] = mapped_column(String(16), index=True)  # verified / one_to_many / split / aggregated / candidate
    matched_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")


class BridgeLayer(Base):
    """跨期桥接：已声明应收 / 可核验到账 / 汇率差异 / 重述影响 / 无法归属 分层发布。"""
    __tablename__ = "bridge_layers"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_runs.id"), index=True)
    receivable_id: Mapped[int | None] = mapped_column(
        ForeignKey("receivable_notices.id"), nullable=True)
    layer: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    reporting_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    reporting_currency: Mapped[str] = mapped_column(String(8), default="CNY")
    fx_status: Mapped[str | None] = mapped_column(String(24), nullable=True)  # applied / missing
    detail_json: Mapped[str] = mapped_column(Text, default="{}")


class AuditSnapshot(Base):
    """审计快照：按截止日封存利润来源、股权版本、决议资料与匹配结果，只读。"""
    __tablename__ = "audit_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("reconciliation_runs.id"), unique=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    seal_hash: Mapped[str] = mapped_column(String(128), index=True)
    frozen_json: Mapped[str] = mapped_column(Text)   # 封存内容全文，重述不回写
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
