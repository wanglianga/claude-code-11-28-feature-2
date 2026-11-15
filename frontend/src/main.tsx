import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, Navigate, RouterProvider } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth'
import Layout from './components/Layout'
import Login from './pages/Login'
import Subjects from './pages/Subjects'
import Rosters from './pages/Rosters'
import Review from './pages/Review'
import Analysis from './pages/Analysis'
import ProfitTrace from './pages/ProfitTrace'
import FinanceLedger from './pages/FinanceLedger'
import './styles.css'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { username } = useAuth()
  if (!username) return <Navigate to="/login" replace />
  return <>{children}</>
}

const router = createBrowserRouter([
  { path: '/login', element: <Login /> },
  {
    path: '/',
    element: (
      <RequireAuth>
        <Layout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/profit-trace" replace /> },
      { path: 'subjects', element: <Subjects /> },
      { path: 'rosters', element: <Rosters /> },
      { path: 'review', element: <Review /> },
      { path: 'analysis', element: <Analysis /> },
      { path: 'profit-trace', element: <ProfitTrace /> },
      { path: 'finance-ledger', element: <FinanceLedger /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  </React.StrictMode>,
)
