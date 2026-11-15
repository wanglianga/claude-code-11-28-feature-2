import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth'

export default function Layout() {
  const { username, role, logout } = useAuth()
  const tabs = [
    { to: '/analysis', label: '股权分析' },
    { to: '/rosters', label: '名册导入' },
    { to: '/subjects', label: '主体与匹配' },
    { to: '/review', label: '修订审核' },
  ]
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          企业股权关系分析平台
          <small>虚构样本 · 不作真实投资或法律结论</small>
        </div>
        <nav>
          {tabs.map((t) => (
            <NavLink key={t.to} to={t.to}
              className={({ isActive }) => (isActive ? 'active' : '')}>
              {t.label}
            </NavLink>
          ))}
        </nav>
        <div className="spacer" />
        <div className="user-box">
          <div>{username}（{role}）</div>
          <button className="ghost small" onClick={logout}>退出登录</button>
        </div>
      </aside>
      <main>
        <Outlet />
      </main>
    </div>
  )
}
