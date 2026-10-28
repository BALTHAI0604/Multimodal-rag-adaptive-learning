import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, errText } from '../../lib/api'
import { Bar, ErrorBox, Label, Loading } from '../../ui/bits.jsx'

const fmtT = (s) => `${String(Math.floor(Math.max(0, s) / 60)).padStart(2, '0')}:${String(Math.max(0, Math.floor(s % 60))).padStart(2, '0')}`

/* ---------------------------------------------------------------- voice recorder (prep timer -> speaking timer -> upload) */
function VoiceAnswer({ q, attemptId, value, onText, language = 'English' }) {
  const [phase, setPhase] = useState('idle')          // idle | prep | recording | uploading | done
  const [left, setLeft] = useState(0)
  const [err, setErr] = useState('')
  const [typed, setTyped] = useState(false)
  const rec = useRef(null), chunks = useRef([]), timer = useRef(null), stream = useRef(null)

  useEffect(() => () => { clearInterval(timer.current); stream.current?.getTracks().forEach((t) => t.stop()) }, [])

  const upload = useCallback(async (blob, secs) => {
    setPhase('uploading')
    const fd = new FormData(); fd.append('audio', blob, 'answer.webm'); fd.append('language', language); fd.append('duration_seconds', String(secs))
    try { const { data } = await api.post(`/student/attempts/${attemptId}/voice/${q.id}`, fd); onText(data.transcript, 'voice'); setPhase('done') }
    catch (e) { setErr(errText(e)); setPhase('idle'); setTyped(true) }
  }, [attemptId, q.id, language, onText])

  const stop = useCallback(() => {
    clearInterval(timer.current)
    if (rec.current?.state === 'recording') rec.current.stop()
  }, [])

  const begin = async () => {
    setErr('')
    try { stream.current = await navigator.mediaDevices.getUserMedia({ audio: true }) }
    catch { setErr('Microphone permission was denied or no microphone was found. You can type your answer instead.'); setTyped(true); return }
    setPhase('prep'); let t = q.voice_prep_sec ?? 10; setLeft(t)
    timer.current = setInterval(() => { t -= 1; setLeft(t); if (t <= 0) { clearInterval(timer.current); record() } }, 1000)
  }
  const record = () => {
    chunks.current = []; const mr = new MediaRecorder(stream.current); rec.current = mr
    const started = Date.now()
    mr.ondataavailable = (e) => chunks.current.push(e.data)
    mr.onstop = () => { stream.current.getTracks().forEach((t) => t.stop()); upload(new Blob(chunks.current, { type: mr.mimeType || 'audio/webm' }), (Date.now() - started) / 1000) }
    mr.start(); setPhase('recording'); let t = q.voice_max_time_sec; setLeft(t)
    timer.current = setInterval(() => { t -= 1; setLeft(t); if (t <= 0) stop() }, 1000)
  }

  return (
    <div className="stack-s">
      {q.voice_guidance && <p className="mute small">{q.voice_guidance}</p>}
      <p className="small mute">You get {q.voice_prep_sec}s to think, then up to {q.voice_max_time_sec}s to speak. Marks are for the ideas in your answer — not your accent or fluency.</p>
      {phase === 'idle' && !typed && <div className="row"><button className="btn accent sm" onClick={begin}>🎙 Start voice answer</button><button className="btn ghost sm" onClick={() => setTyped(true)}>Type instead</button></div>}
      {phase === 'prep' && <div className="card"><span className="label">Prepare</span><div className="timer">{fmtT(left)}</div><button className="btn ghost sm" onClick={() => { clearInterval(timer.current); record() }}>Skip prep</button></div>}
      {phase === 'recording' && <div className="card"><div className="row"><span className="rec" /><span className="label">Recording</span><span className="timer">{fmtT(left)}</span></div><button className="btn sm" style={{ marginTop: 10 }} onClick={stop}>Stop & submit</button></div>}
      {phase === 'uploading' && <p className="mute">Transcribing…</p>}
      {(phase === 'done' || typed) && <div className="field"><label htmlFor={`v${q.id}`}>{phase === 'done' ? 'Transcript — check it and fix any mistakes' : 'Your answer'}</label>
        <textarea id={`v${q.id}`} className="input" value={value || ''} onChange={(e) => onText(e.target.value, phase === 'done' ? 'voice' : 'typed')} /></div>}
      <ErrorBox msg={err} />
    </div>
  )
}

/* ---------------------------------------------------------------- question renderers */
function Question({ q, ans, setAns, attemptId }) {
  const set = (patch) => setAns(q.id, patch)
  const hasOptions = q.options?.length > 0
  const body = (() => {
    if (q.question_type === 'voice') return <VoiceAnswer q={q} attemptId={attemptId} value={ans?.answer_text} onText={(t, mode) => set({ answer_text: t, input_mode: mode })} />
    if (hasOptions) return (
      <div className="stack-s" role="radiogroup">{q.options.map((o) => (
        <label key={o.id} className={`opt ${ans?.selected_option_id === o.id ? 'sel' : ''}`}>
          <input type="radio" name={`q${q.id}`} checked={ans?.selected_option_id === o.id} onChange={() => set({ selected_option_id: o.id })} /> <span>{o.text}</span></label>))}</div>)
    if (q.question_type === 'match_following') return (
      <div className="stack-s">{q.match_left.map((l) => (
        <div key={l.id} className="row"><span style={{ flex: '1 1 200px' }}>{l.text}</span>
          <select className="input" style={{ flex: '1 1 240px' }} aria-label={`Match for ${l.text}`} value={ans?.answer_json?.pairs?.[l.id] || ''} onChange={(e) => set({ answer_json: { pairs: { ...(ans?.answer_json?.pairs || {}), [l.id]: e.target.value } } })}>
            <option value="">Select…</option>{q.match_right.map((r) => <option key={r.id} value={r.id}>{r.text}</option>)}</select></div>))}</div>)
    if (q.question_type === 'sequence') {
      const order = ans?.answer_json?.order || q.sequence_items.map((i) => i.id)
      const byId = Object.fromEntries(q.sequence_items.map((i) => [i.id, i.text]))
      const move = (i, d) => { const o = [...order]; const j = i + d; if (j < 0 || j >= o.length) return;[o[i], o[j]] = [o[j], o[i]]; set({ answer_json: { order: o } }) }
      return <ol className="stack-s" style={{ paddingLeft: 0, listStyle: 'none' }}>{order.map((id, i) => (
        <li key={id} className="opt"><span className="mono">{i + 1}</span><span style={{ flex: 1 }}>{byId[id]}</span>
          <button className="btn ghost sm" aria-label="Move up" onClick={() => move(i, -1)}>↑</button><button className="btn ghost sm" aria-label="Move down" onClick={() => move(i, 1)}>↓</button></li>))}</ol>
    }
    const long = !['fill_blank', 'numerical'].includes(q.question_type)
    return long ? <textarea className="input" aria-label="Your answer" value={ans?.answer_text || ''} onChange={(e) => set({ answer_text: e.target.value, input_mode: 'typed' })} />
      : <input className="input" aria-label="Your answer" inputMode={q.question_type === 'numerical' ? 'decimal' : 'text'} value={ans?.answer_text || ''} onChange={(e) => set({ answer_text: e.target.value, input_mode: 'typed' })} />
  })()
  return (
    <div className="card page-enter" key={q.id}>
      <div className="row between"><span className="label">{q.question_type.replace('_', ' ')} · {q.bloom_level}</span><span className="pill">{q.marks} mark{q.marks === 1 ? '' : 's'}</span></div>
      <h2 className="h2" style={{ margin: '14px 0 18px' }}>{q.question_text}</h2>
      {q.question_type === 'assertion_reason' && <div className="card" style={{ marginBottom: 14, background: 'var(--bg)' }}><p><b>Assertion:</b> {q.assertion_text}</p><p><b>Reason:</b> {q.reason_text}</p></div>}
      {body}
    </div>
  )
}

/* ---------------------------------------------------------------- page */
export default function Take() {
  const { id } = useParams()
  const nav = useNavigate()
  const [meta, setMeta] = useState(null)
  const [session, setSession] = useState(null)
  const [answers, setAnswers] = useState({})
  const [idx, setIdx] = useState(0)
  const [err, setErr] = useState('')
  const [left, setLeft] = useState(null)
  const [warn, setWarn] = useState('')
  const [micOk, setMicOk] = useState(false)
  const [busy, setBusy] = useState(false)
  const saveTimers = useRef({})
  const submitting = useRef(false)

  useEffect(() => { api.get('/student/assessments').then((r) => setMeta(r.data.find((a) => a.id === Number(id)) || { id: Number(id), title: 'Practice', has_voice: false, require_fullscreen: false, time_limit_minutes: null })).catch((e) => setErr(errText(e))) }, [id])

  const start = async () => {
    setBusy(true); setErr('')
    try {
      if (meta?.require_fullscreen && document.documentElement.requestFullscreen) { try { await document.documentElement.requestFullscreen() } catch { /* user may refuse; logged as an event */ } }
      const { data } = await api.post(`/student/assessments/${id}/start`, { preflight: meta?.require_preflight || meta?.has_voice ? { mic_ok: micOk, camera_ok: false, camera_consent: false, fullscreen_supported: !!document.documentElement.requestFullscreen } : null })
      const init = {}
      Object.entries(data.saved || {}).forEach(([qid, s]) => { init[qid] = { answer_text: s.text ?? undefined, selected_option_id: s.selected_option_id ?? undefined, answer_json: s.json ?? undefined } })
      setAnswers(init); setSession(data)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  // server-synchronised countdown
  const offset = useMemo(() => session ? new Date(session.server_time).getTime() - Date.now() : 0, [session])
  useEffect(() => {
    if (!session) return
    const end = new Date(session.deadline_at).getTime()
    const tick = () => { const s = (end - (Date.now() + offset)) / 1000; setLeft(s); if (s <= 0) submit(true) }
    tick(); const t = setInterval(tick, 500); return () => clearInterval(t)
  }, [session]) // eslint-disable-line

  const setAns = useCallback((qid, patch) => {
    setAnswers((a) => ({ ...a, [qid]: { ...a[qid], ...patch } }))
    clearTimeout(saveTimers.current[qid])
    saveTimers.current[qid] = setTimeout(() => {
      const payload = { question_id: qid, ...patch }
      api.put(`/student/attempts/${session.attempt_id}/answer`, payload).catch((e) => setErr(errText(e)))
    }, 450)
  }, [session])

  const submit = async (auto = false) => {
    if (submitting.current) return
    if (!auto && !confirm('Submit your answers now? You cannot change them afterwards.')) return
    submitting.current = true; setBusy(true)
    Object.values(saveTimers.current).forEach(clearTimeout)
    const list = Object.entries(answers).map(([qid, a]) => ({ question_id: Number(qid), selected_option_id: a.selected_option_id, answer_text: a.answer_text, answer_json: a.answer_json, input_mode: a.input_mode }))
    try {
      await api.post(`/student/attempts/${session.attempt_id}/submit`, { answers: list })
      if (document.fullscreenElement) document.exitFullscreen?.()
      nav(`/app/result/${session.attempt_id}`, { replace: true })
    } catch (e) { setErr(errText(e)); submitting.current = false; setBusy(false) }
  }

  // Integrity signals. These are browser events only: they are logged for faculty review and nothing is punished automatically.
  useEffect(() => {
    if (!session) return
    const report = (type) => api.post(`/student/attempts/${session.attempt_id}/violations`, { violation_type: type, client_time: new Date().toISOString() })
      .then(({ data }) => { setWarn(data.action === 'log' ? '' : `Event recorded (${data.count}/${data.limit}). Your faculty will review it; no automatic penalty applies.`); if (data.action === 'submit') nav(`/app/result/${session.attempt_id}`, { replace: true }) }).catch(() => {})
    const vis = () => document.hidden && report('tab_hidden')
    const blur = () => report('window_blur')
    const fs = () => { if (meta?.require_fullscreen && !document.fullscreenElement) report('fullscreen_exit') }
    const paste = () => report('copy_paste')
    document.addEventListener('visibilitychange', vis); window.addEventListener('blur', blur); document.addEventListener('fullscreenchange', fs); document.addEventListener('paste', paste)
    return () => { document.removeEventListener('visibilitychange', vis); window.removeEventListener('blur', blur); document.removeEventListener('fullscreenchange', fs); document.removeEventListener('paste', paste) }
  }, [session, meta, nav])

  const testMic = async () => { try { const s = await navigator.mediaDevices.getUserMedia({ audio: true }); s.getTracks().forEach((t) => t.stop()); setMicOk(true) } catch { setErr('Microphone not available. You can still type your voice answers if your institution allows.') } }

  if (!meta && !err) return <Loading />
  if (!session) return (
    <main className="page wrap page-enter" id="main"><Label n={1}>Before you begin</Label><h1 className="h1" style={{ margin: '12px 0 24px' }}>{meta?.title}</h1>
      <div className="grid g2"><div className="card stack-s"><p>{meta?.questions ? `${meta.questions} questions · ` : ''}{meta?.time_limit_minutes ? `${meta.time_limit_minutes} minutes. The timer is kept by the server.` : ''}</p>
        <p className="mute small">Your answers autosave. Scoring happens on the server; correct answers stay hidden until release.</p>
        <p className="mute small"><b>Integrity:</b> your browser reports when you switch tab, leave the window or exit fullscreen. These events are recorded for your faculty to review. Nothing is closed or penalised automatically, and no face analysis is performed.</p></div>
        <div className="card stack-s">{meta?.has_voice && <><p>This assessment has a spoken answer.</p><button className="btn ghost sm" onClick={testMic}>{micOk ? '✓ Microphone works' : 'Test microphone'}</button></>}
          {meta?.require_fullscreen && <p className="small mute">Fullscreen is requested for this assessment.</p>}
          <ErrorBox msg={err} /><button className="btn accent" disabled={busy} onClick={start} data-cursor="Begin">{busy ? 'Starting…' : 'Begin'} →</button></div></div></main>)

  const qs = session.questions, q = qs[idx]
  const answered = qs.filter((x) => { const a = answers[x.id]; return a && (a.selected_option_id || a.answer_text || a.answer_json) }).length
  return (
    <main className="page wrap" id="main">
      <div className="row between" style={{ position: 'sticky', top: 70, zIndex: 20, background: 'var(--bg)', padding: '8px 0', borderBottom: '1px solid var(--line)' }}>
        <span className="label">Question {idx + 1} / {qs.length} · answered {answered}</span>
        <span className={`timer ${left < 60 ? 'low' : ''}`} aria-live="off" role="timer">{left == null ? '' : fmtT(left)}</span></div>
      <Bar value={(answered / qs.length) * 100} className="" />
      {warn && <p className="pill warn" role="status" style={{ margin: '12px 0' }}>{warn}</p>}
      <div style={{ marginTop: 20 }}><Question q={q} ans={answers[q.id]} setAns={setAns} attemptId={session.attempt_id} /></div>
      <ErrorBox msg={err} />
      <div className="row between" style={{ marginTop: 20 }}>
        <button className="btn ghost" disabled={idx === 0} onClick={() => setIdx(idx - 1)}>← Previous</button>
        <div className="row">{qs.map((x, i) => <button key={x.id} aria-label={`Go to question ${i + 1}`} className="pill" style={{ cursor: 'pointer', background: i === idx ? 'var(--text)' : 'transparent', color: i === idx ? 'var(--bg)' : undefined }} onClick={() => setIdx(i)}>{i + 1}</button>)}</div>
        {idx < qs.length - 1 ? <button className="btn" onClick={() => setIdx(idx + 1)}>Next →</button> : <button className="btn accent" disabled={busy} onClick={() => submit(false)}>Submit</button>}</div>
    </main>
  )
}
