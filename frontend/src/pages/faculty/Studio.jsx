import { useState } from 'react'
import { api, errText } from '../../lib/api'
import { useGet } from '../../lib/hooks'
import { ErrorBox, Label, Loading, Page, fmt } from '../../ui/bits.jsx'

const ALL_TYPES = ['mcq', 'true_false', 'fill_blank', 'numerical', 'match_following', 'sequence', 'assertion_reason', 'short_answer', 'analytical', 'scenario', 'application', 'voice']
const QFIELDS = ['question_type', 'question_text', 'bloom_level', 'difficulty', 'marks', 'explanation', 'options', 'match_pairs_json', 'sequence_items_json', 'assertion_text', 'reason_text', 'accepted_answers_json', 'numeric_answer', 'numeric_tolerance', 'answer_key_text', 'rubric_json', 'voice_guidance', 'voice_prep_sec', 'voice_max_time_sec']
const toPayload = (q) => Object.fromEntries(QFIELDS.filter((k) => q[k] !== null && q[k] !== undefined).map((k) => [k, k === 'options' ? q.options.map((o) => ({ option_text: o.option_text, is_correct: o.is_correct, explanation: o.explanation, misconception: o.misconception })) : q[k]]))

export default function Studio() {
  const subs = useGet('/faculty/subjects')
  const list = useGet('/faculty/assessments')
  const [open, setOpen] = useState(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [f, setF] = useState({ subject_id: '', unit_id: '', topic_id: '', class_id: '', title: '', num_questions: 8, difficulty: 'medium', time_limit_minutes: 20, types: ['mcq', 'true_false', 'fill_blank', 'short_answer'] })
  const subject = subs.data?.find((s) => s.id === Number(f.subject_id))
  const run = async (fn, ok = '') => { setMsg(''); setBusy(true); try { const r = await fn(); if (ok) setMsg(ok); list.reload(); return r } catch (e) { setMsg(errText(e)) } finally { setBusy(false) } }
  const load = (id) => run(async () => setOpen((await api.get(`/faculty/assessments/${id}`)).data))
  const generate = (e) => {
    e.preventDefault()
    run(async () => { const b = { ...f, subject_id: +f.subject_id, unit_id: +f.unit_id, topic_id: +f.topic_id, class_id: f.class_id ? +f.class_id : null, num_questions: +f.num_questions, time_limit_minutes: +f.time_limit_minutes }; const { data } = await api.post('/faculty/assessments/generate', b); setOpen(data) }, 'Draft created. Review every question before publishing.')
  }
  const updQ = (i, patch) => setOpen((o) => ({ ...o, questions: o.questions.map((q, j) => j === i ? { ...q, ...patch } : q) }))
  const saveQ = (q) => run(async () => { await api.put(`/faculty/assessments/${open.id}/questions/${q.id}`, toPayload(q)); await load(open.id) }, 'Question saved.')
  const regen = (q) => run(async () => { await api.post(`/faculty/assessments/${open.id}/questions/${q.id}/regenerate`); await load(open.id) }, 'Question regenerated.')
  const del = (q) => run(async () => { await api.delete(`/faculty/assessments/${open.id}/questions/${q.id}`); await load(open.id) })
  const publish = () => {
    const end = prompt('Closing date/time (e.g. 2026-10-20 17:00) — leave blank for no deadline:')
    const body = end ? { end_time: new Date(end).toISOString().slice(0, 19) } : {}
    run(async () => { await api.post(`/faculty/assessments/${open.id}/publish`, body); await load(open.id) }, 'Published to students.')
  }
  const draft = open?.status === 'draft'
  if (subs.loading) return <Loading />
  return (
    <Page label="Assessment studio" title="Assessments">
      <p className={msg.startsWith('Draft') || msg.endsWith('saved.') || msg.endsWith('students.') || msg.endsWith('regenerated.') ? 'okmsg' : 'err'} role="status">{msg}</p>
      {!open && <>
        <Label n={1}>Generate from material</Label>
        <form className="card stack" style={{ marginTop: 14 }} onSubmit={generate}>
          <div className="grid g3">
            <div className="field"><label htmlFor="s">Subject</label><select id="s" className="input" required value={f.subject_id} onChange={(e) => setF({ ...f, subject_id: e.target.value, unit_id: '', topic_id: '' })}><option value="">Select…</option>{subs.data?.map((s) => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}</select></div>
            <div className="field"><label htmlFor="u2">Unit</label><select id="u2" className="input" required value={f.unit_id} onChange={(e) => setF({ ...f, unit_id: e.target.value, topic_id: '' })}><option value="">Select…</option>{subject?.units.map((u) => <option key={u.id} value={u.id}>Unit {u.unit_number}: {u.title}</option>)}</select></div>
            <div className="field"><label htmlFor="t2">Topic</label><select id="t2" className="input" required value={f.topic_id} onChange={(e) => setF({ ...f, topic_id: e.target.value })}><option value="">Select…</option>{subject?.units.find((u) => u.id === +f.unit_id)?.topics.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}</select></div>
            <div className="field"><label htmlFor="c2">Class</label><select id="c2" className="input" value={f.class_id} onChange={(e) => setF({ ...f, class_id: e.target.value })}><option value="">All classes</option>{subject?.classes.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
            <div className="field"><label htmlFor="ti2">Title</label><input id="ti2" className="input" required minLength={3} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
            <div className="field"><label htmlFor="n">Questions</label><input id="n" type="number" min="1" max="30" className="input" value={f.num_questions} onChange={(e) => setF({ ...f, num_questions: e.target.value })} /></div>
            <div className="field"><label htmlFor="d">Difficulty</label><select id="d" className="input" value={f.difficulty} onChange={(e) => setF({ ...f, difficulty: e.target.value })}><option>easy</option><option>medium</option><option>hard</option></select></div>
            <div className="field"><label htmlFor="tl">Time limit (min)</label><input id="tl" type="number" min="1" max="240" className="input" value={f.time_limit_minutes} onChange={(e) => setF({ ...f, time_limit_minutes: e.target.value })} /></div></div>
          <div className="row">{ALL_TYPES.map((t) => <label key={t} className={`pill ${f.types.includes(t) ? 'acc' : ''}`} style={{ cursor: 'pointer' }}><input type="checkbox" hidden checked={f.types.includes(t)} onChange={() => setF({ ...f, types: f.types.includes(t) ? f.types.filter((x) => x !== t) : [...f.types, t] })} />{t.replace('_', ' ')}</label>)}</div>
          <p className="small mute">Questions come only from approved, indexed material of this topic. If Ollama is running it drafts them; otherwise template extraction is used and labelled. Numerical questions need the model.</p>
          <div><button className="btn accent" disabled={busy || !f.topic_id || !f.types.length}>{busy ? 'Generating…' : 'Generate draft'} →</button></div></form>
        <div className="divider" /><Label n={2}>Your assessments</Label>
        <ErrorBox msg={list.error} />
        <div className="table-wrap"><table><thead><tr><th>Title</th><th>Status</th><th>Questions</th><th>Attempts</th><th>Window</th><th /></tr></thead><tbody>{list.data?.map((a) => (
          <tr key={a.id}><td>{a.title}</td><td><span className={`pill ${a.status === 'published' ? 'ok' : ''}`}>{a.status}</span></td><td>{a.question_count}</td><td>{a.attempts}</td><td className="small">{fmt(a.start_time)} → {fmt(a.end_time)}</td><td><button className="btn ghost sm" onClick={() => load(a.id)}>Open</button></td></tr>))}</tbody></table></div></>}

      {open && <>
        <div className="row between"><div><Label n={1}>{open.status}</Label><h2 className="h1" style={{ fontSize: 40 }}>{open.title}</h2><p className="mute small">{open.question_count ?? open.questions.length} questions · {open.total_marks} marks · {open.generation_info?.llm_used ? 'drafted by the language model' : 'template extraction (model offline)'}{open.generation_info?.dropped_invalid ? ` · ${open.generation_info.dropped_invalid} invalid model items discarded` : ''}</p></div>
          <div className="row"><button className="btn ghost" onClick={() => setOpen(null)}>← All</button>
            {draft && <button className="btn accent" disabled={busy} onClick={publish}>Approve & publish →</button>}
            {open.status === 'published' && <button className="btn danger" onClick={() => confirm('Close now? Running attempts are submitted and answer keys are released.') && run(async () => { await api.post(`/faculty/assessments/${open.id}/close`); await load(open.id) }, 'Closed.')}>Close & release answers</button>}</div></div>
        <div className="stack" style={{ marginTop: 24 }}>{open.questions.map((q, i) => (
          <div key={q.id} className="card">
            <div className="row between"><span className="label">Q{i + 1} · {q.question_type.replace('_', ' ')} · {q.bloom_level} · {q.generation_method}</span>
              {draft && <div className="row"><button className="btn ghost sm" onClick={() => regen(q)}>Regenerate</button><button className="btn danger sm" onClick={() => del(q)}>Delete</button><button className="btn sm" onClick={() => saveQ(q)}>Save</button></div>}</div>
            <textarea className="input" style={{ margin: '12px 0' }} disabled={!draft} value={q.question_text} onChange={(e) => updQ(i, { question_text: e.target.value })} aria-label="Question text" />
            {q.assertion_text && <p className="small">Assertion: {q.assertion_text}<br />Reason: {q.reason_text}</p>}
            {q.options.map((o, j) => <div key={j} className="row" style={{ marginBottom: 6 }}><input type="radio" disabled={!draft} checked={o.is_correct} aria-label="Correct option" onChange={() => updQ(i, { options: q.options.map((x, k) => ({ ...x, is_correct: k === j })) })} /><input className="input" style={{ flex: 1 }} disabled={!draft} value={o.option_text} onChange={(e) => updQ(i, { options: q.options.map((x, k) => k === j ? { ...x, option_text: e.target.value } : x) })} aria-label={`Option ${j + 1}`} /></div>)}
            {q.match_pairs_json && <p className="small mute">Pairs: {q.match_pairs_json.map((p) => `${p.left} → ${p.right}`).join(' · ')}</p>}
            {q.sequence_items_json && <p className="small mute">Order: {q.sequence_items_json.map((p) => p.text).join(' → ')}</p>}
            {q.accepted_answers_json && <p className="small mute">Accepted: {q.accepted_answers_json.join(', ')}</p>}
            {q.answer_key_text && <p className="small mute">Model answer: {q.answer_key_text}</p>}
            {q.rubric_json && <p className="small mute">Rubric: {q.rubric_json.key_points?.map((k) => k.text).join(' | ')}</p>}
            <div className="row small mute" style={{ marginTop: 8 }}>Marks <input type="number" step="0.5" min="0.5" className="input" style={{ maxWidth: 90 }} disabled={!draft} value={q.marks} onChange={(e) => updQ(i, { marks: +e.target.value })} aria-label="Marks" />{q.source_refs?.length ? <span>Sources: chunk {q.source_refs.join(', ')}</span> : null}</div>
          </div>))}</div></>}
    </Page>
  )
}
