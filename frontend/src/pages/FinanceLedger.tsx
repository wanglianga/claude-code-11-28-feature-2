import { useEffect, useState } from 'react'
import { api } from '../api'
import { StatusBadge, num, pct } from '../components/badges'

interface Statement {
  id: number
  company_name: string | null
  source_ref: string
  period_code: string
  period_start: string
  period_end: string
  currency: string
  status: string
  staging: string
  restates_statement_id: number | null
  total_positive: string
  lines: any[]
}

interface Snapshot {
  id: number
  company_name: string | null
  snapshot_date: string
  source_ref: string
  status: string
  holdings: any[]
}

interface Resolution {
  id: number
  company_name: string | null
  resolution_no: string
  declaration_date: string
  record_date: string
  share_class: string
  total_declared_amount: string
  currency: string
  funding_period_code: string | null
  status: string
  lines: any[]
}

interface Receivable {
  id: number
  receivable_no: string
  issuer_name: string | null
  collecting_name: string | null
  amount: string
  currency: string
  confirmed_date: string
  resolution_id: number | null
}

interface Receipt {
  id: number
  receipt_no: string
  collecting_name: string | null
  payer_label: string
  amount: string
  currency: string
  direction: string
  value_date: string
  memo: string
}

interface FxRate {
  id: number
  from_currency: string
  to_currency: string
  rate: string | null
  approved: boolean
  source_ref: string
  effective_date: string | null
}

export default function FinanceLedger() {
  const [stmts, setStmts] = useState<Statement[]>([])
  const [snaps, setSnaps] = useState<Snapshot[]>([])
  const [resolutions, setResolutions] = useState<Resolution[]>([])
  const [receivables, setReceivables] = useState<Receivable[]>([])
  const [receipts, setReceipts] = useState<Receipt[]>([])
  const [fx, setFx] = useState<FxRate[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([
      api<Statement[]>('/finance/profit-statements'),
      api<Snapshot[]>('/finance/equity-snapshots'),
      api<Resolution[]>('/finance/resolutions'),
      api<Receivable[]>('/finance/receivables'),
      api<Receipt[]>('/finance/receipts'),
      api<FxRate[]>('/finance/fx-rates'),
    ])
      .then(([a, b, c, d, e, f]) => {
        setStmts(a); setSnaps(b); setResolutions(c)
        setReceivables(d); setReceipts(e); setFx(f)
      })
      .catch((e) => setError(String(e)))
  }, [])

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>财务事实资料</h2>
          <div className="desc">
            利润表、股权快照、分红决议、应收通知、银行回单与获批汇率分别登记；
            重述保留原表原值，审计快照封存后不随重述变化。
          </div>
        </div>
      </div>
      {error && <div className="alert error">{error}</div>}

      <div className="panel">
        <div className="panel-title">利润表（按会计期间；重述保留原值）</div>
        <div className="panel-body" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr><th>公司</th><th>会计期间</th><th>口径</th><th>状态</th>
                <th className="right">正数利润合计</th><th>来源</th></tr>
            </thead>
            <tbody>
              {stmts.map((s) => (
                <tr key={s.id}>
                  <td>{s.company_name}</td>
                  <td className="nowrap mono">{s.period_code}<div className="muted" style={{ fontSize: 11 }}>{s.period_start} ~ {s.period_end}</div></td>
                  <td>
                    <span className={`badge ${s.staging === 'restated' ? 'violet' : 'gray'}`}>
                      {s.staging === 'restated' ? '重述后' : '原始'}
                    </span>
                    {s.restates_statement_id && <div className="muted" style={{ fontSize: 11 }}>重述 #{s.restates_statement_id}</div>}
                  </td>
                  <td><StatusBadge status={s.status === 'restated' ? 'approved' : s.status} /></td>
                  <td className="right mono">{num(s.total_positive)} {s.currency}</td>
                  <td>
                    <details className="sub">
                      <summary>{s.source_ref}（{s.lines.length} 行）</summary>
                      <div className="sub-body">
                        {s.lines.map((l) => (
                          <div key={l.id} className="kv">
                            第 {l.line_no} 行 · {l.line_category} · {l.label}：
                            <b>{num(l.amount)}</b>
                            {l.payer_company_subject_id ? `（付款公司 #${l.payer_company_subject_id}）` : ''}
                          </div>
                        ))}
                      </div>
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">时点股权快照（来源分配只认记录日可确认快照）</div>
        <div className="panel-body" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>公司</th><th>登记日</th><th>状态</th><th>持股（按股份类别）</th></tr></thead>
            <tbody>
              {snaps.map((s) => (
                <tr key={s.id}>
                  <td>{s.company_name}</td>
                  <td className="mono nowrap">{s.snapshot_date}</td>
                  <td><StatusBadge status={s.status === 'confirmable' ? 'approved' : 'pending'} /></td>
                  <td>
                    {s.holdings.map((h) => (
                      <span key={h.id} className="badge gray" style={{ marginRight: 6 }}>
                        {h.holder_label} {h.share_class} {pct(h.ratio, 2)}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">董事会分红决议（记录日与会计期间独立）</div>
        <div className="panel-body" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr><th>决议号</th><th>公司</th><th>记录日 / 决议日</th><th>资金期间</th>
                <th className="right">宣告总额</th><th>状态</th></tr>
            </thead>
            <tbody>
              {resolutions.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.resolution_no}</td>
                  <td>{r.company_name}</td>
                  <td className="nowrap mono">{r.record_date}<div className="muted" style={{ fontSize: 11 }}>决议 {r.declaration_date}</div></td>
                  <td><span className="badge blue">{r.funding_period_code ?? '—'}</span></td>
                  <td className="right mono">{num(r.total_declared_amount)} {r.currency}</td>
                  <td><StatusBadge status={r.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid-2">
        <div className="panel">
          <div className="panel-title">应收通知（按确认日）</div>
          <div className="panel-body" style={{ padding: 0 }}>
            <table>
              <thead><tr><th>应收号</th><th>关系</th><th className="right">金额</th><th>确认日</th></tr></thead>
              <tbody>
                {receivables.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.receivable_no}</td>
                    <td style={{ fontSize: 12 }}>{r.issuer_name} → {r.collecting_name}</td>
                    <td className="right mono">{num(r.amount)} {r.currency}</td>
                    <td className="mono nowrap">{r.confirmed_date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel">
          <div className="panel-title">银行回单（按到账日）</div>
          <div className="panel-body" style={{ padding: 0 }}>
            <table>
              <thead><tr><th>回单号</th><th>付款方</th><th className="right">金额</th><th>到账日</th></tr></thead>
              <tbody>
                {receipts.map((b) => (
                  <tr key={b.id}>
                    <td className="mono" style={{ fontSize: 11 }}>{b.receipt_no}</td>
                    <td style={{ fontSize: 12 }}>{b.payer_label}</td>
                    <td className="right mono">{num(b.amount)} {b.currency}</td>
                    <td className="mono nowrap">{b.value_date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">获批汇率来源（未获批不得用于换算）</div>
        <div className="panel-body" style={{ padding: 0 }}>
          <table>
            <thead><tr><th>币种对</th><th className="right">汇率</th><th>生效日</th><th>状态</th><th>来源</th></tr></thead>
            <tbody>
              {fx.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.from_currency} → {r.to_currency}</td>
                  <td className="right mono">{r.rate ?? '—'}</td>
                  <td className="mono nowrap">{r.effective_date ?? '—'}</td>
                  <td>
                    <span className={`badge ${r.approved ? 'green' : 'red'}`}>
                      {r.approved ? '已获批' : '未获批'}
                    </span>
                  </td>
                  <td className="mono muted" style={{ fontSize: 11 }}>{r.source_ref}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
