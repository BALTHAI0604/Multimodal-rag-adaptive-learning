import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, errText } from '../../lib/api'
import { useGet } from '../../lib/hooks'
import { CopyButton, ErrorBox, Label, Page } from '../../ui/bits.jsx'

const LANGS = { English: 'en-IN', Tamil: 'ta-IN', Kannada: 'kn-IN', Malayalam: 'ml-IN', Hindi: 'hi-IN' }
const startersFor = (topic) => topic ? [`Summarise ${topic} in five bullet points`, `What are the key terms in ${topic}?`, `Explain ${topic} with a simple example`] : ['What topics are covered in my notes?', 'Summarise my latest notes']
const KIND = { grounded: ['Grounded in your material', 'ok'], extractive: ['Passages from your material (AI model offline)', 'warn'], insufficient_material: ['Not in the material', 'bad'], general: ['General knowledge — not from your course', 'warn'] }

const withCites = (t) => String(t).split(/(\[\d{1,3}\])/g).map((part, i) => /^\[\d{1,3}\]$/.test(part) ? <sup key={i} className="cite">{part}</sup> : part)

export default function Companion() {
  const [sp] = useSearchParams()
  const subs = useGet('/student/subjects')
  const caps = useGet('/rag/capabilities')
  const dash = useGet('/student/dashboard')
  const [subject, setSubject] = useState(sp.get('subject') || '')
  const [lang, setLang] = useState('English')
  const [text, setText] = useState('')
  const [log, setLog] = useState([])
  const [sid, setSid] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [general, setGeneral] = useState(false)
  const [listening, setListening] = useState(false)
  const end = useRef(null)
  const recRef = useRef(null)
  useEffect(() => { if (log.length) end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [log])

  const ask = async (msg = text, voice = false) => {
    if (!msg.trim()) return
    setErr(''); setBusy(true); setText(''); setLog((l) => [...l, { role: 'u', text: msg }])
    try {
      const { data } = await api.post('/rag/companion', { message: msg, language: lang, subject_id: subject ? Number(subject) : null, topic_id: sp.get('topic') ? Number(sp.get('topic')) : null, session_id: sid, is_voice: voice, allow_general: general })
      setSid(data.session_id); setLog((l) => [...l, { role: 'a', ...data }])
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const speak = async (t) => {
    try { const r = await api.post('/rag/tts', { text: t, language: lang }, { responseType: 'blob' }); new Audio(URL.createObjectURL(r.data)).play() }
    catch { // server voice not installed: fall back to the browser's own voices
      if ('speechSynthesis' in window) { const u = new SpeechSynthesisUtterance(t); u.lang = LANGS[lang]; speechSynthesis.speak(u) } else setErr('No text-to-speech voice is available for this language.')
    }
  }
  const listen = async () => {
    if (listening) { recRef.current?.stop(); return }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true }); const chunks = []; const mr = new MediaRecorder(s); recRef.current = mr
      mr.ondataavailable = (e) => chunks.push(e.data)
      mr.onstop = async () => { s.getTracks().forEach((t) => t.stop()); setListening(false)
        const fd = new FormData(); fd.append('audio', new Blob(chunks, { type: 'audio/webm' }), 'q.webm'); fd.append('language', lang)
        try { const { data } = await api.post('/rag/transcribe', fd); setText(data.text) } catch (e) { setErr(errText(e)) } }
      mr.start(); setListening(true)
    } catch { setErr('Microphone not available.') }
  }
  const clear = async () => { if (confirm('Delete all your saved AI conversations?')) { await api.delete('/rag/conversations'); setLog([]); setSid(null) } }

  return (
    <Page label="Companion" title="Ask your material." actions={<button className="btn ghost sm" onClick={clear}>Clear history</button>}>
      <div className="row" style={{ marginBottom: 16 }}>
        <select className="input" style={{ maxWidth: 280 }} value={subject} onChange={(e) => setSubject(e.target.value)} aria-label="Subject"><option value="">All my subjects</option>{subs.data?.map((s) => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}</select>
        <select className="input" style={{ maxWidth: 180 }} value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Language">{Object.keys(LANGS).map((l) => <option key={l}>{l}</option>)}</select>
        <label className="small mute row"><input type="checkbox" checked={general} onChange={(e) => setGeneral(e.target.checked)} /> allow clearly-labelled general answers</label>
      </div>
      {caps.data && !caps.data.llm.available && <p className="pill warn" style={{ marginBottom: 12 }}>AI model offline — you will see matching passages instead of written answers.</p>}
      {caps.data && !caps.data.embeddings.semantic && <p className="pill warn" style={{ marginBottom: 12, marginLeft: 8 }}>Keyword search mode (semantic model not installed)</p>}
      <div className="card" role="log" aria-live="polite" aria-label="Conversation" style={{ minHeight: 360, display: 'grid', gap: 14, alignContent: 'start' }}>
        {!log.length && <div className="stack"><p className="mute">Ask anything about today’s topic. I answer only from material your faculty approved, and I show where each answer came from.</p>
          <div className="chips" aria-label="Suggested questions">{startersFor(dash.data?.today_topic?.title).map((s) => <button key={s} type="button" className="chip" onClick={() => ask(s)} disabled={busy}>{s}</button>)}</div></div>}
        {log.map((m, i) => m.role === 'u' ? <div key={i} className="bubble u">{m.text}</div> : (
          <div key={i} className="bubble a"><span className={`pill ${KIND[m.answer_type]?.[1]}`}>{KIND[m.answer_type]?.[0]}</span>
            <p style={{ margin: '10px 0', whiteSpace: 'pre-wrap' }}>{withCites(m.answer)}</p>
            {m.citation_warning && <p className="small" style={{ color: 'var(--warn)' }}>⚠ {m.citation_warning}</p>}
            {m.sources?.length > 0 && <details className="stack-s" open={m.sources.length <= 2}><summary className="small mute" style={{ cursor: 'pointer' }}>{m.sources.length} source{m.sources.length > 1 ? 's' : ''} from your material</summary>{m.sources.map((s) => <p key={s.n} className="small mute src">[{s.n}] <b>{s.title}</b>{s.page_ref ? ` · ${s.page_ref}` : ''} — “{s.snippet}”</p>)}</details>}
            <div className="row" style={{ marginTop: 10 }}><button className="btn ghost sm" onClick={() => speak(m.answer)}>🔊 Listen</button><CopyButton text={m.answer} /></div></div>))}
        {busy && <div className="bubble a" role="status" aria-label="The companion is thinking"><span className="typing"><i /><i /><i /></span></div>}<div ref={end} />
      </div>
      <ErrorBox msg={err} />
      <form className="row" style={{ marginTop: 14 }} onSubmit={(e) => { e.preventDefault(); ask() }}>
        <input className="input" style={{ flex: 1 }} placeholder="Type your question… (Enter to send)" autoFocus value={text} onChange={(e) => setText(e.target.value)} aria-label="Your question" maxLength={2000} />
        <button type="button" className="btn ghost" onClick={listen} aria-pressed={listening}>{listening ? '■ Stop' : '🎙'}</button>
        <button className="btn accent" disabled={busy || !text.trim()}>Ask →</button>
      </form>
      <Label n={1}>Answers cite numbered passages. If the material does not cover a question, I will say so.</Label>
    </Page>
  )
}
