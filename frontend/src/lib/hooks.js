import { useEffect, useRef, useState, useCallback } from 'react'
import { api, errText } from './api'

/** GET helper with loading/error state and manual reload. */
export function useGet(url, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const load = useCallback(() => {
    if (!url) return
    setLoading(true)
    api.get(url).then((r) => { setData(r.data); setError('') })
      .catch((e) => setError(errText(e))).finally(() => setLoading(false))
  }, [url]) // eslint-disable-line
  useEffect(load, [load, ...deps]) // eslint-disable-line
  return { data, error, loading, reload: load, setData }
}

/** Adds .in to elements when scrolled into view (the scroll-reveal used across the site). */
export function useReveal() {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target) } }), { threshold: 0.12 })
    el.querySelectorAll('.reveal,.mask,.tl-item').forEach((n) => io.observe(n))
    return () => io.disconnect()
  })
  return ref
}

export function useToast() {
  const [msg, setMsg] = useState('')
  const show = useCallback((m) => { setMsg(m); setTimeout(() => setMsg(''), 3600) }, [])
  return [msg, show]
}
