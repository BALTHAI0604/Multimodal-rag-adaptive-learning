import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../lib/auth.jsx'
import { api, errText } from '../lib/api'
import { Label, Lines } from '../ui/bits.jsx'

export default function Login() {
  const { user, login } = useAuth()
  const nav = useNavigate()
  const loc = useLocation()
  const [id, setId] = useState('')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [info, setInfo] = useState('')
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState('login')
  if (user) return <Navigate to={loc.state?.from || '/app'} replace />

  const submit = async (e) => {
    e.preventDefault(); setErr(''); setInfo(''); setBusy(true)
    try {
      if (mode === 'login') { await login(id, pw); nav(loc.state?.from || '/app') }
      else { const { data } = await api.post('/auth/password-reset', { identifier: id }); setInfo(data.message) }
    } catch (e2) { setErr(errText(e2)) } finally { setBusy(false) }
  }
  return (
    <main className="wrap" style={{ minHeight: '100svh', display: 'grid', alignContent: 'center', gap: 40, paddingBlock: 80 }} id="main">
      <Link to="/" className="label">← Back</Link>
      <Lines className="h1" lines={[mode === 'login' ? 'Welcome back.' : 'Reset access.']} />
      <form onSubmit={submit} className="stack" style={{ maxWidth: 460 }} noValidate>
        <Label n={1}>{mode === 'login' ? 'Sign in with your e-mail or student / staff ID' : 'Enter your e-mail or ID'}</Label>
        <div className="field"><label htmlFor="id">E-mail or ID</label><input id="id" className="input" autoComplete="username" value={id} onChange={(e) => setId(e.target.value)} required /></div>
        {mode === 'login' && <div className="field"><label htmlFor="pw">Password</label><input id="pw" className="input" type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} required /></div>}
        {err && <p className="err" role="alert">{err}</p>}
        {info && <p className="okmsg" role="status">{info}</p>}
        <div className="row">
          <button className="btn accent" disabled={busy || !id || (mode === 'login' && !pw)} data-cursor="Go">{busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Send reset link'} <span className="arrow">→</span></button>
          <button type="button" className="btn ghost sm" onClick={() => { setMode(mode === 'login' ? 'reset' : 'login'); setErr(''); setInfo('') }}>{mode === 'login' ? 'Forgot password?' : 'Back to sign in'}</button>
        </div>
        <p className="mute small">There is no self sign-up. Your administrator creates your account; first-time users set a password from the e-mailed link.</p>
      </form>
    </main>
  )
}
