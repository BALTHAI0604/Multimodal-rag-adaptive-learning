import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { api, setToken, setAuthLostHandler } from './api'

const Ctx = createContext(null)
export const useAuth = () => useContext(Ctx)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    setAuthLostHandler(() => setUser(null))
    api.post('/auth/refresh').then(({ data }) => { setToken(data.access_token); setUser(data.user) })
      .catch(() => {}).finally(() => setReady(true))
  }, [])

  const login = useCallback(async (identifier, password) => {
    const { data } = await api.post('/auth/login', { identifier, password })
    setToken(data.access_token); setUser(data.user); return data.user
  }, [])
  const logout = useCallback(async () => {
    try { await api.post('/auth/logout') } catch { /* already signed out */ }
    setToken(null); setUser(null)
  }, [])
  return <Ctx.Provider value={{ user, ready, login, logout, setUser }}>{children}</Ctx.Provider>
}
