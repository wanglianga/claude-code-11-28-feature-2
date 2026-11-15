import { useEffect, useState } from 'react'
import { api, ApiError, detailText } from '../api'
import { StatusBadge } from '../components/badges'
import { useAuth } from '../auth'

interface Subject {
  id: number
  name: string
  subject_type: string
  unified_code: string | null
  status: string
  merged_into_id: number | null
}

interface Candidate {
  id: number
  source_subject_id: number
  target_subject_id: number
  match_reason: string
  name_similarity: string
  status: string
  conflict_note: string | null
  decided_by: string | null
}

const TYPE_TEXT: Record<string, string> = {
  company: '企业', person: '自然人', institution: '机构',
}

export default function Subjects() {
  const { role } = useAuth()
  const canEdit = role === 'analyst' || role === 'admin'
  const [subjects, setSubjects] = useState<Subject[]>([])
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [form, setForm] = useState({ name: '', subject_type: 'company', unified_code: '' })

  async function load() {
    setSubjects(await api('/subjects'))
    setCandidates(await api('/merge-candidates'))
  }
  useEffect(() => { load().catch((e) => setError(String(e))) }, [])

  const nameOf = (id: number) => subjects.find((s) => s.id === id)?.name ?? `#${id}`

  async function createSubject(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    try {
      await api('/subjects', {
        method: 'POST',
        body: JSON.stringify({
          ...form,
          unified_code: form.unified_code || null,
        }),
      })
      setInfo('主体已建档；若存在同名主体，系统已生成匹配候选')
      setForm({ name: '', subject_type: 'company', unified_code: '' })
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  async function decide(id: number, confirm: boolean) {
    setError('')
    setInfo('')
    const note = confirm ? '人工核对确认归并' : '人工核对为不同主体'
    try {
      await api(`/merge-candidates/${id}/decision`, {
        method: 'POST',
        body: JSON.stringify({ confirm, note }),
      })
      setInfo(confirm ? '合并已生效，源主体名下持有记录已改写' : '候选已驳回')
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  async function scan() {
    try {
      const r = await api<{ new_candidates: number }>('/merge-candidates/scan', { method: 'POST' })
      setInfo(`模糊扫描完成，新增 ${r.new_candidates} 个候选`)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : String(err))
    }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>主体库与同名匹配</h2>
          <div className="desc">同名只产生候选，统一社会标识不一致禁止归并；归并必须人工确认并核对关系修订</div>
        </div>
        {canEdit && <button className="ghost" onClick={scan}>扫描近似名称</button>}
      </div>

      {error && <div className="alert error">{error}</div>}
      {info && <div className="alert ok">{info}</div>}

      <div className="grid-2">
        <div className="panel">
          <div className="panel-title">主体列表（{subjects.length}）</div>
          <div className="panel-body" style={{ padding: 0 }}>
            <table>
              <thead><tr><th>ID</th><th>名称</th><th>类型</th><th>统一社会标识</th><th>状态</th></tr></thead>
              <tbody>
                {subjects.map((s) => (
                  <tr key={s.id}>
                    <td>{s.id}</td>
                    <td>{s.name}{s.merged_into_id && <div className="muted">并入 → {nameOf(s.merged_into_id)}</div>}</td>
                    <td>{TYPE_TEXT[s.subject_type] ?? s.subject_type}</td>
                    <td className="mono">{s.unified_code ?? <span className="muted">无（未知/待识别）</span>}</td>
                    <td><StatusBadge status={s.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div>
          {canEdit && (
            <div className="panel">
              <div className="panel-title">新建主体</div>
              <div className="panel-body">
                <form onSubmit={createSubject}>
                  <label className="field">
                    <span>名称</span>
                    <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
                  </label>
                  <label className="field">
                    <span>类型</span>
                    <select value={form.subject_type}
                      onChange={(e) => setForm({ ...form, subject_type: e.target.value })}>
                      <option value="company">企业</option>
                      <option value="person">自然人</option>
                      <option value="institution">机构</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>统一社会标识（可空，留空表示暂无标识）</span>
                    <input value={form.unified_code}
                      onChange={(e) => setForm({ ...form, unified_code: e.target.value })} />
                  </label>
                  <button>建档</button>
                </form>
              </div>
            </div>
          )}

          <div className="panel">
            <div className="panel-title">匹配候选（{candidates.length}）</div>
            <div className="panel-body" style={{ padding: 0 }}>
              <table>
                <thead><tr><th>主体对</th><th>依据</th><th>状态</th><th>操作</th></tr></thead>
                <tbody>
                  {candidates.map((c) => (
                    <tr key={c.id}>
                      <td>
                        {nameOf(c.source_subject_id)}
                        <div className="muted">↔ {nameOf(c.target_subject_id)}</div>
                        {c.conflict_note && <div className="alert warn" style={{ marginTop: 6, padding: '6px 9px' }}>{c.conflict_note}</div>}
                      </td>
                      <td>
                        {c.match_reason === 'exact_name' ? '完全同名' : '近似名称'}
                        <div className="muted">相似度 {Number(c.name_similarity).toFixed(3)}</div>
                      </td>
                      <td><StatusBadge status={c.status} /></td>
                      <td>
                        {canEdit && ['pending', 'conflict'].includes(c.status) && (
                          <div className="row-flex">
                            <button className="small success" onClick={() => decide(c.id, true)}>确认合并</button>
                            <button className="small ghost" onClick={() => decide(c.id, false)}>驳回</button>
                          </div>
                        )}
                        {!['pending', 'conflict'].includes(c.status) && (
                          <span className="muted">{c.decided_by ?? ''}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {candidates.length === 0 && (
                    <tr><td colSpan={4} className="muted" style={{ padding: 20 }}>暂无候选</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
