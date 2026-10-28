import { Component, useEffect, useRef, useState } from 'react'
import { useReveal } from '../lib/hooks'

export const Reveal = ({ children, d = 0, as: T = 'div', className = '', ...p }) => {
  const r = useRef(null)
  useEffect(() => {
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { r.current?.classList.add('in'); io.disconnect() } }, { threshold: 0.12 })
    r.current && io.observe(r.current)
    return () => io.disconnect()
  }, [])
  return <T ref={r} className={`reveal ${className}`} style={{ '--d': `${d}s` }} {...p}>{children}</T>
}

/** Headline whose lines slide up out of a mask. */
export const Lines = ({ lines, className = 'display' }) => {
  const r = useRef(null)
  useEffect(() => { const t = setTimeout(() => r.current?.classList.add('in'), 60); return () => clearTimeout(t) }, [])
  return (
    <h1 ref={r} className={className} aria-label={lines.join(' ')}>
      {lines.map((l, i) => <span key={i} className="mask" aria-hidden><span style={{ '--d': `${i * 0.09}s` }}>{l}</span></span>)}
    </h1>
  )
}

export const Label = ({ n, children }) => <div className="label">{n != null && <b>({String(n).padStart(2, '0')})</b>} {n != null && '— '}{children}</div>

export const Marquee = ({ items }) => {
  const row = [...items, ...items]
  return (
    <div className="marquee" aria-hidden>
      <div className="marquee-track">{row.map((t, i) => <span key={i}>{t} <i>✦</i></span>)}</div>
    </div>
  )
}

export const Accordion = ({ items }) => {
  const [open, setOpen] = useState(0)
  return items.map((it, i) => (
    <div key={i} className={`acc ${open === i ? 'open' : ''}`}>
      <button aria-expanded={open === i} onClick={() => setOpen(open === i ? -1 : i)}>
        <span>{it.q}</span><span className="plus">+</span>
      </button>
      <div className="body"><div>{it.a}</div></div>
    </div>
  ))
}

/** Custom cursor that grows and shows a label over elements carrying data-cursor="…". Disabled on touch. */
export const Cursor = () => {
  const el = useRef(null)
  const [label, setLabel] = useState('')
  useEffect(() => {
    if (matchMedia('(hover:none)').matches) return
    let x = 0, y = 0, tx = 0, ty = 0, raf
    const move = (e) => { tx = e.clientX; ty = e.clientY; const t = e.target.closest?.('[data-cursor]'); setLabel(t ? t.dataset.cursor : '') }
    const loop = () => { x += (tx - x) * 0.2; y += (ty - y) * 0.2; if (el.current) el.current.style.transform = `translate(${x}px,${y}px)`; raf = requestAnimationFrame(loop) }
    addEventListener('mousemove', move); raf = requestAnimationFrame(loop)
    return () => { removeEventListener('mousemove', move); cancelAnimationFrame(raf) }
  }, [])
  return <div ref={el} className={`cursor ${label ? 'big' : ''}`} aria-hidden>{label}</div>
}

export const Bar = ({ value, className = '' }) => {
  const v = Math.max(0, Math.min(100, value ?? 0))
  return <div className={`bar ${v < 50 ? 'low' : v < 70 ? 'mid' : ''} ${className}`} role="progressbar" aria-valuenow={v}><i style={{ width: `${v}%` }} /></div>
}

export const Empty = ({ title, children }) => (
  <div className="card" style={{ textAlign: 'center', padding: '48px 24px' }}>
    <div className="h3">{title}</div><p className="mute" style={{ marginTop: 8 }}>{children}</p>
  </div>
)

export const Page = ({ label, n = 1, title, children, actions }) => {
  const ref = useReveal()
  return (
    <main ref={ref} className="page wrap page-enter" id="main">
      <div className="page-title row between" style={{ alignItems: 'flex-end' }}>
        <div className="stack-s"><Label n={n}>{label}</Label><h1 className="h1">{title}</h1></div>
        {actions}
      </div>
      {children}
    </main>
  )
}

export const Loading = () => (
  <main className="page wrap" role="status" aria-live="polite" aria-busy="true">
    <span className="sr-only">Loading…</span>
    <div className="skel" style={{ height: 56, maxWidth: 420, marginBottom: 28 }} />
    <div className="grid g4"><div className="skel" /><div className="skel" /><div className="skel" /><div className="skel" /></div>
  </main>
)

/** Accessible dialog: Escape closes, focus moves inside and returns to the opener. */
export const Modal = ({ title, children, onClose }) => {
  const ref = useRef(null)
  useEffect(() => {
    const prev = document.activeElement
    ref.current?.focus()
    const key = (e) => { if (e.key === 'Escape') onClose() }
    addEventListener('keydown', key)
    return () => { removeEventListener('keydown', key); prev?.focus?.() }
  }, [onClose])
  return (
    <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal stack" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <h2 className="h3">{title}</h2>{children}
      </div>
    </div>
  )
}

export const CopyButton = ({ text, label = 'Copy', className = 'btn ghost sm' }) => {
  const [done, setDone] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(text) } catch {
      const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select(); try { document.execCommand('copy') } catch { /* ignore */ } t.remove()
    }
    setDone(true); setTimeout(() => setDone(false), 1800)
  }
  return <button type="button" className={className} onClick={copy} aria-live="polite">{done ? 'Copied ✓' : label}</button>
}

/** Catches render errors so a bug never leaves a blank white page. */
export class ErrorBoundary extends Component {
  state = { error: null }
  static getDerivedStateFromError(error) { return { error } }
  componentDidCatch(error) { console.error('UI error:', error) }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <main className="page wrap" role="alert">
        <div className="callout bad stack-s"><h1 className="h3">Something went wrong on this page.</h1>
          <p className="mute">Your data is safe. Try again, or go back to your dashboard.</p>
          <div className="row"><button className="btn accent sm" onClick={() => { this.setState({ error: null }) }}>Try again</button>
            <a className="btn ghost sm" href="/app">Dashboard</a></div></div>
      </main>
    )
  }
}
export const ErrorBox = ({ msg }) => msg ? <p className="err" role="alert">{msg}</p> : null
export const fmt = (iso) => iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—'
