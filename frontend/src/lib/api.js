import axios from 'axios'

// Access token lives in memory only. The refresh token is an httpOnly cookie the browser sends to /api/auth.
let accessToken = null
let onAuthLost = () => {}
export const setToken = (t) => { accessToken = t }
export const setAuthLostHandler = (fn) => { onAuthLost = fn }

export const api = axios.create({
  baseURL: 'https://multimodal-rag-adaptive-learning.onrender.com/api',
  withCredentials: true
})
api.interceptors.request.use((cfg) => {
  if (accessToken) cfg.headers.Authorization = `Bearer ${accessToken}`
  return cfg
})

let refreshing = null
api.interceptors.response.use((r) => r, async (error) => {
  const { config, response } = error
  const isAuthCall = config?.url?.startsWith('/auth/')
  if (response?.status === 401 && config && !config._retry && !isAuthCall) {
    config._retry = true
    try {
      refreshing = refreshing || api.post('/auth/refresh').finally(() => { refreshing = null })
      const { data } = await refreshing
      setToken(data.access_token)
      return api(config)
    } catch {
      setToken(null)
      onAuthLost()
    }
  }
  return Promise.reject(error)
})

export const errText = (e, fallback = 'Something went wrong.') => {
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (e?.response?.data?.errors?.length) return e.response.data.errors.map((x) => `${x.field}: ${x.message}`).join('; ')
  return e?.message === 'Network Error' ? 'Cannot reach the server. Is the backend running?' : fallback
}
