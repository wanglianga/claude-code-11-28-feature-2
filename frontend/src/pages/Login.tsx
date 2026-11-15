import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'
import { ApiError, detailText } from '../api'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('analyst')
  const [password, setPassword] = useState('analyst123')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
      navigate('/analysis')
    } catch (err) {
      setError(err instanceof ApiError ? detailText(err.detail) : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h1>企业股权关系分析平台</h1>
        <div className="sub">集团持股关系追溯 · 经济权益与表决控制分离计算</div>
        {error && <div className="alert error">{error}</div>}
        <label className="field">
          <span>用户名</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="field">
          <span>密码</span>
          <input type="password" value={password}
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        <button style={{ width: '100%' }} disabled={loading}>
          {loading ? '登录中…' : '登 录'}
        </button>
        <div className="login-hint">
          演示账号（角色分离）：<br />
          analyst / analyst123（分析员：导入、合并、发起分析）<br />
          reviewer / reviewer123（审核员：审核修订、发布报告）<br />
          admin / admin123（兼两者，可一键播种样本）
        </div>
      </form>
    </div>
  )
}
