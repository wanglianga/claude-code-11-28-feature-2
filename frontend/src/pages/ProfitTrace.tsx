import { useEffect, useState } from 'react'
import { api, ApiError, detailText } from '../api'
import { StatusBadge, num } from '../components/badges'
import { useAuth } from '../auth'

interface ReconSummary {
  id: number
  as_of_date: string
  status: string
  created_by: string
  created_at: string
  published_at: string | null
}

interface Receivable {
  id: number
  receivable_no: string
  issuer_name: string | null
  collecting_name: string | null
  amount: string
  currency: string
  confirmed_date: string
}

interface Chain {
  id: number
  receivable_id: number
  entry_kind: string
  terminal_company_name: string | null
  terminal_line_id: number | null
  path_weight: string
  amount: string
  currency: string
  path: any[]
  detail: any
}

interface Match {
  id: number
  receipt_id: number
  receipt_no: string | null
  receivable_id: number | null
  rule: string
  status: string
  matched_amount: string
  currency: string
  verified: boolean
  reason: string
  detail: any
}

interface Bridge {
  id: number
  receivable_id: number | null
  layer: string
  amount: string | null
  currency: string
  reporting_amount: string | null
  reporting_currency: string
  fx_status: string | null
  detail: any
}

interface Audit {
  id: number
  as_of_date: string
  seal_hash: string
  created_by: string
  created_at: string
  frozen: any
}

interface ReconDetail {
  id: number
  as_of_date: string
  status: string
  diagnostics: any
  receivables: Receivable[]
  chains: Chain[]
  matches: Match[]
  bridges: Bridge[]
  layer_totals: Record<string, Record<string, number>>
  layer_missing: Record<string, any[]>
  audit_snapshot: Audit | null
}

interface Timeline {
  entries: { kind: string; date: string | null; label: string; ref: any }[]
  cross_period_facts: any[]
  note: string
}

const KIND_STYLE: Record<string, string> = {
  operating_line: 'green',
  cycle_residual: 'violet',
  title_pending: 'amber',
  unallocatable: 'red',
}
const KIND_TEXT: Record<string, string> = {
  operating_line: '经营利润来源',
  cycle_residual: '交叉持股残差',
  title_pending: '权属待证',
  unallocatable: '无法归属',
}

const LAYER_TEXT: Record<string, string> = {
  declared_receivable: '① 已声明应收',
  verified_receipt: '② 可核验到账',
  fx_difference: '③ 汇率差异',
  restatement_impact: '④ 重述影响',
  unallocatable: '⑤ 无法归属',
}

const DATE_KIND_TEXT: Record<string, string> = {
  profit_period: '利润表会计期间',
  resolution_record_date: '决议记录日',
  receivable_confirmed_date: '应收确认日',
  receipt_value_date: '回单到账日',
}

export default function ProfitTrace() {
  const { role } = useAuth()
  const canRun = role === 'analyst' || role === 'admin'
  const canSeal = role === 'reviewer' || role === 'admin'
  const [runs, setRuns] = useState<ReconSummary[]>([])
  const [detail, setDetail] = useState<ReconDetail | null>(null)
  const [timeline, setTimeline] = useState<Timeline | null>(null)
  const [asOf, setAsOf] = useState('2026-06-30')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [tab, setTab] = useState<'trace' | 'match' | 'bridge' | 'timeline'>('trace')

  async function loadRuns(selectId?: number) {
    const list = await api<ReconSummary[]>('/finance/reconciliation-runs')
    setRuns(list)
    if (selectId) await openRun(selectId)
    else if (list.length && !detail) await openRun(list[0].id)
  }

  useEffect(() => {
    loadRuns().catch((e) => setError(String(e)))
    api<Timeline>('/finance/timeline').then(setTimeline).catch(() => undefined)
  }, [])

  async function openRun(id: number) {
    setDetail(await api<ReconDetail>(`/finance/reconciliation-runs/${id}`))
  }

  async function createRun() {
    setError(''); setInfo('')
    try {
      const r = await api<any>('/finance/reconciliation-runs', {
        method: 'POST', body: JSON.stringify({ as_of_date: asOf }),
      })
      setInfo(`对账运行 #${r.run_id} 已创建`)
      await loadRuns(r.run_id)
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  async function seal(id: number) {
    setError(''); setInfo('')
    try {
      const r = await api<any>(`/finance/reconciliation-runs/${id}/seal`, { method: 'POST' })
      setInfo(`审计快照 #${r.id} 已封存，哈希 ${r.seal_hash.slice(0, 16)}…（只读）`)
      await loadRuns(id)
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  function recvLabel(id: number | null): string {
    if (id === null) return '—'
    const r = detail?.receivables.find((x) => x.id === id)
    return r ? `${r.receivable_no}` : `#${id}`
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>利润来源穿透与跨期对账</h2>
          <div className="desc">
            按决议记录日股权快照与股份类别确认权属，沿利润构成穿透到经营来源；回单按外部证据匹配；
            五层桥接区分已声明、可核验、汇率、重述与无法归属。不执行分红批准、付款或资金调度。
          </div>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {info && <div className="alert ok">{info}</div>}
      <div className="disclaimer">
        所有企业、金额、文号、回单号均为虚构样本。系统只发布可复算的财务事实与差异，
        不构成真实分红、税务或法律结论。
      </div>

      <div className="panel">
        <div className="panel-title">对账运行</div>
        <div className="panel-body">
          <div className="form-inline">
            {canRun && (
              <>
                <label className="field">
                  <span>分析截止日</span>
                  <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
                </label>
                <button onClick={createRun}>按此截止日对账</button>
              </>
            )}
            <label className="field" style={{ minWidth: 260 }}>
              <span>历史运行</span>
              <select value={detail?.id ?? ''} onChange={(e) => openRun(Number(e.target.value))}>
                {runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    #{r.id} {r.as_of_date} · {r.status}{r.status === 'sealed' ? ' · 已封存' : ''}
                  </option>
                ))}
              </select>
            </label>
            {detail && canSeal && !detail.audit_snapshot && (
              <button className="success" onClick={() => seal(detail.id)}>封存审计快照</button>
            )}
          </div>
          <div className="pill-list" style={{ marginTop: 12 }}>
            {runs.map((r) => (
              <button key={r.id} className={`small ${detail?.id === r.id ? '' : 'ghost'}`}
                onClick={() => openRun(r.id)}>
                #{r.id} {r.as_of_date} <StatusBadge status={r.status} />
              </button>
            ))}
          </div>
        </div>
      </div>

      {detail && (
        <>
          {detail.audit_snapshot && (
            <div className="alert info">
              本运行已封存审计快照 #{detail.audit_snapshot.id}（截止 {detail.audit_snapshot.as_of_date}），
              哈希 <span className="mono">{detail.audit_snapshot.seal_hash.slice(0, 24)}…</span>。
              后续重述只生成跨期差异，不回写该快照。
            </div>
          )}
          <div className="grid-3">
            <div className="stat"><div className="num">{detail.receivables.length}</div><div className="lbl">应收通知（按确认日）</div></div>
            <div className="stat">
              <div className="num">{detail.diagnostics?.pending_titles?.length ?? 0}</div>
              <div className="lbl">权属待证（记录日无快照，禁止借用最近一期）</div>
            </div>
            <div className="stat">
              <div className="num">{detail.diagnostics?.one_to_many_conflicts?.length ?? 0}</div>
              <div className="lbl">一对多冲突（已排除出可核验到账）</div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-title">
              <span>分析导航</span>
            </div>
            <div className="panel-body">
              <div className="pill-list">
                {([
                  ['trace', '来源链与残差'],
                  ['match', '回单匹配'],
                  ['bridge', '五层桥接'],
                  ['timeline', '四类日期时间线'],
                ] as const).map(([k, label]) => (
                  <button key={k} className={`small ${tab === k ? '' : 'ghost'}`}
                    onClick={() => setTab(k)}>{label}</button>
                ))}
              </div>
            </div>
          </div>

          {tab === 'trace' && (
            <div className="panel">
              <div className="panel-title">来源穿透：从应收经权属到经营利润</div>
              <div className="panel-body" style={{ padding: 0 }}>
                <table>
                  <thead>
                    <tr>
                      <th>应收</th><th>链类型</th><th className="right">金额（原币）</th>
                      <th>终到利润表行 / 路径</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.chains.map((c) => {
                      const recv = detail.receivables.find((r) => r.id === c.receivable_id)
                      return (
                        <tr key={c.id}>
                          <td className="nowrap">
                            <div>{recv?.receivable_no ?? `#${c.receivable_id}`}</div>
                            <div className="muted" style={{ fontSize: 11 }}>
                              {recv?.issuer_name} → {recv?.collecting_name}
                            </div>
                          </td>
                          <td><span className={`badge ${KIND_STYLE[c.entry_kind] ?? 'gray'}`}>
                            {KIND_TEXT[c.entry_kind] ?? c.entry_kind}
                          </span></td>
                          <td className="right mono">{num(c.amount)} {c.currency}</td>
                          <td>
                            {c.entry_kind === 'operating_line' ? (
                              <details className="sub">
                                <summary>
                                  利润表行 #{c.terminal_line_id} · {c.terminal_company_name ?? ''}
                                </summary>
                                <div className="sub-body">
                                  {c.path.map((step, i) => (
                                    <div key={i} className="kv" style={{ marginBottom: 6 }}>
                                      {i + 1}. {step.company_subject_id} · {step.period_code}
                                      {' '}第 {step.line_no} 行「{step.label}」
                                      {' '}金额 {num(step.line_amount)}，权重 {Number(step.weight).toFixed(6)}
                                    </div>
                                  ))}
                                  <div className="kv">路径权重合计：<b>{Number(c.path_weight).toFixed(6)}</b></div>
                                </div>
                              </details>
                            ) : (
                              <div className="kv">
                                {c.detail?.reason}
                                {c.detail?.cycle_path && (
                                  <div>循环路径：{[...new Set(c.detail.cycle_path)].join(' → ')}（残差单列）</div>
                                )}
                              </div>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {tab === 'match' && (
            <div className="panel">
              <div className="panel-title">回单匹配：外部证据、拆分、汇聚与一对多冲突</div>
              <div className="panel-body" style={{ padding: 0 }}>
                <table>
                  <thead>
                    <tr>
                      <th>回单</th><th>应收</th><th>规则</th><th>结论</th>
                      <th className="right">金额</th><th>理由（事实）</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.matches.map((m) => (
                      <tr key={m.id}>
                        <td className="mono nowrap">{m.receipt_no ?? `#${m.receipt_id}`}</td>
                        <td className="nowrap">{m.receivable_id ? recvLabel(m.receivable_id) : '—'}</td>
                        <td><span className="badge gray">{m.rule}</span></td>
                        <td>
                          <span className={`badge ${m.status === 'one_to_many' ? 'red' : m.verified ? 'green' : 'amber'}`}>
                            {m.status}
                          </span>
                          {!m.verified && <div className="muted" style={{ fontSize: 11 }}>未核验</div>}
                        </td>
                        <td className="right mono">{num(m.matched_amount)} {m.currency}</td>
                        <td className="muted" style={{ fontSize: 12 }}>{m.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {tab === 'bridge' && (
            <>
              <div className="panel">
                <div className="panel-title">五层桥接汇总（按币种分别发布，不混合）</div>
                <div className="panel-body" style={{ padding: 0 }}>
                  <table>
                    <thead><tr><th>层</th><th>币种与金额</th><th>缺失 / 提示</th></tr></thead>
                    <tbody>
                      {['declared_receivable', 'verified_receipt', 'fx_difference', 'restatement_impact', 'unallocatable'].map((layer) => (
                        <tr key={layer}>
                          <td className="nowrap"><b>{LAYER_TEXT[layer]}</b></td>
                          <td>
                            {detail.layer_totals[layer]
                              ? Object.entries(detail.layer_totals[layer]).map(([cur, amt]) => (
                                <div key={cur} className="mono">{num(amt)} {cur}</div>
                              ))
                              : <span className="muted">—</span>}
                          </td>
                          <td style={{ fontSize: 12 }}>
                            {(detail.layer_missing[layer] ?? []).map((mi: any, i: number) => (
                              <div key={i} className="kv">
                                {recvLabel(mi.receivable_id)} · {mi.detail?.message ?? mi.detail?.reason ?? '缺失'}
                              </div>
                            ))}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="panel">
                <div className="panel-title">按应收展开的桥接行</div>
                <div className="panel-body" style={{ padding: 0 }}>
                  <table>
                    <thead>
                      <tr><th>应收</th><th>层</th><th className="right">原币</th>
                        <th className="right">报告币</th><th>汇率状态</th></tr>
                    </thead>
                    <tbody>
                      {detail.bridges.map((b) => (
                        <tr key={b.id}>
                          <td className="nowrap">{recvLabel(b.receivable_id)}</td>
                          <td><span className="badge blue">{LAYER_TEXT[b.layer] ?? b.layer}</span></td>
                          <td className="right mono">
                            {b.amount === null ? '—' : `${num(b.amount)} ${b.currency}`}
                          </td>
                          <td className="right mono">
                            {b.reporting_amount === null ? <span className="muted">缺失汇率</span>
                              : `${num(b.reporting_amount)} ${b.reporting_currency}`}
                          </td>
                          <td>
                            {b.fx_status === 'missing'
                              ? <span className="badge red">缺失汇率</span>
                              : b.fx_status === 'applied'
                                ? <span className="badge green">已获批</span>
                                : <span className="muted">—</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}

          {tab === 'timeline' && timeline && (
            <>
              <div className="alert info">{timeline.note}</div>
              <div className="panel">
                <div className="panel-title">四类日期分别建模（不把同月视为同一事实）</div>
                <div className="panel-body" style={{ padding: 0 }}>
                  <table>
                    <thead><tr><th>日期</th><th>类型</th><th>事实</th><th>引用</th></tr></thead>
                    <tbody>
                      {timeline.entries.map((e, i) => (
                        <tr key={i}>
                          <td className="mono nowrap">{e.date}</td>
                          <td><span className="badge gray">{DATE_KIND_TEXT[e.kind] ?? e.kind}</span></td>
                          <td>{e.label}</td>
                          <td className="muted mono" style={{ fontSize: 11 }}>
                            {Object.entries(e.ref).filter(([, v]) => v != null).slice(0, 3)
                              .map(([k, v]) => `${k}=${v}`).join(' ')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div className="panel">
                <div className="panel-title">跨期到账事实</div>
                <div className="panel-body">
                  {timeline.cross_period_facts.length === 0 && <div className="muted">无跨期到账</div>}
                  {timeline.cross_period_facts.map((c, i) => (
                    <div key={i} className="alert warn" style={{ marginBottom: 8 }}>
                      {c.receivable_no}（确认 {c.receivable_confirmed_date}，期间 {c.receivable_period}）
                      → 回单 {c.receipt_no}（到账 {c.receipt_value_date}，期间 {c.receipt_period}）。
                      {c.reason}
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
