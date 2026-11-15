import React, { createContext, useContext, useMemo, useState } from 'react'
import { api, NAME_KEY, ROLE_KEY, TOKEN_KEY } from './api'

interface AuthState {
  username: string | null
  role: string | null
  login: (username: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthState>(null as unknown as AuthState)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [username, setUsername] = useState<string | null>(localStorage.getItem(NAME_KEY))
  const [role, setRole] = useState<string | null>(localStorage.getItem(ROLE_KEY))

  const value = useMemo<AuthState>(
    () => ({
      username,
      role,
      async login(u, p) {
        const data = await api<{ access_token: string; role: string }>('/auth/login', {
          method: 'POST',
          body: JSON.stringify({ username: u, password: p }),
        })
        localStorage.setItem(TOKEN_KEY, data.access_token)
        localStorage.setItem(ROLE_KEY, data.role)
        localStorage.setItem(NAME_KEY, u)
        setUsername(u)
        setRole(data.role)
      },
      logout() {
        localStorage.removeItem(TOKEN_KEY)
        localStorage.removeItem(ROLE_KEY)
        localStorage.removeItem(NAME_KEY)
        setUsername(null)
        setRole(null)
      },
    }),
    [username, role],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}
