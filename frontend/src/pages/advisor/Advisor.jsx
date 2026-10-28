import { useState } from 'react'
import { api, errText } from '../../lib/api'
import { useGet } from '../../lib/hooks'
import { Bar, ErrorBox, Label, Loading, Page, fmt } from '../../ui/bits.jsx'

export default function Advisor() {
  const { data, loading, error, reload } = useGet('/advisor/dashboard')
  const [sel, setSel] = useState(null)
  const [d, setD] = useState(null)
  const [form, setForm] = useState({ observation: '', recommendation: '', intervention_required: false, intervention_plan: '' })
  const [msg, setMsg] = useState('')
  const open = async (id) => { setSel(id); try { setD((await api.get(`/advisor/students/${id}`)).data) } catch (e) { setMsg(errText(e)) } }
  const save = async (e) => {
    e.preventDefault(); setMsg('')
    try { await api.post('/advisor/reviews', { student_id: sel, ...form, intervention_plan: form.intervention_plan || null }); setForm({ observation: '', recommendation: '', intervention_required: false, intervention_plan: '' }); open(sel); reload(); setMsg('Saved.') } catch (e2) { setMsg(errText(e2)) }
  }
  const upd = async (id, status) => { try { await api.patch(`/advisor/reviews/${id}/intervention`, { intervention_status: status }); open(sel); reload() } catch (e) { setMsg(errText(e)) } }
  if (loading) return <Loading />
  return (
    <Page label="Advisor" title="Your students">
      <ErrorBox msg={error || (msg !== 'Saved.' ? msg : '')} />
      {!d && <>
        <div className="grid g3"><div className="card"><div className="label">Assigned</div><div className="stat">{data.total_students}</div></div><div className="card"><div className="label">Flagged</div><div className="stat">{data.flagged}</div></div><div className="card"><div className="label">Open interventions</div><div className="stat">{data.open_interventions}</div></div></div>
        <div className="divider" />
        <div className="table-wrap"><table><thead><tr><th>ID</th><th>Name</th><th>Class</th><th>Avg mastery</th><th>Weak topics</th><th>Streak</th><th /></tr></thead><tbody>{data.students.map((s) => (
          <tr key={s.student_id}><td className="mono">{s.roll_number}</td><td>{s.name}</td><td>{s.class_name}</td><td style={{ minWidth: 140 }}>{s.avg_mastery != null ? <><Bar value={s.avg_mastery} /><span className="small">{s.avg_mastery}%</span></> : <span className="mute">no data</span>}</td><td>{s.weak_topics}</td><td>{s.streak}</td>
            <td className="row">{s.flag && <span className="pill bad" title={s.flag_reason}>flagged</span>}<button className="btn ghost sm" onClick={() => open(s.student_id)}>Open</button></td></tr>))}</tbody></table></div></>}
      {d && <div className="stack">
        <div className="row between"><div><Label n={1}>{d.roll_number}</Label><h2 className="h1" style={{ fontSize: 44 }}>{d.name}</h2></div><button className="btn ghost" onClick={() => { setD(null); setSel(null) }}>← All students</button></div>
        <div className="grid g2">{d.subjects.map((s) => <div key={s.code} className="card"><div className="row between"><span className="h3">{s.subject}</span><span className="pill">{s.avg_mastery}%</span></div>{s.topics.map((t) => <p key={t.topic} className="small row between"><span>{t.topic}</span><span className="mute">{Math.round(t.mastery)}% · {t.trend.replace('_', ' ')}</span></p>)}</div>)}
          {!d.subjects.length && <p className="mute">No assessment data yet.</p>}</div>
        {d.misconceptions.length > 0 && <div className="card"><Label n={2}>Open misconceptions</Label>{d.misconceptions.map((m, i) => <p key={i} className="small" style={{ marginTop: 8 }}>{m.count}× {m.text}</p>)}</div>}
        {d.teacher_reviews.length > 0 && <div className="card"><Label n={3}>Faculty reviews</Label>{d.teacher_reviews.map((r, i) => <p key={i} className="small" style={{ marginTop: 8 }}><b>{r.subject}</b> · {r.rating.replace('_', ' ')} — {r.comment} {r.follow_up_status === 'open' && <span className="pill warn">follow-up open</span>}</p>)}</div>}
        <form className="card stack" onSubmit={save}><Label n={4}>New advisor review</Label>
          <div className="field"><label htmlFor="o">Observation</label><textarea id="o" required minLength={3} className="input" value={form.observation} onChange={(e) => setForm({ ...form, observation: e.target.value })} /></div>
          <div className="field"><label htmlFor="r">Recommendation</label><textarea id="r" required minLength={3} className="input" value={form.recommendation} onChange={(e) => setForm({ ...form, recommendation: e.target.value })} /></div>
          <label className="row small"><input type="checkbox" checked={form.intervention_required} onChange={(e) => setForm({ ...form, intervention_required: e.target.checked })} /> Intervention required</label>
          {form.intervention_required && <div className="field"><label htmlFor="p">Intervention plan</label><textarea id="p" className="input" value={form.intervention_plan} onChange={(e) => setForm({ ...form, intervention_plan: e.target.value })} /></div>}
          <div className="row"><button className="btn accent">Save review</button>{msg === 'Saved.' && <span className="okmsg">Saved.</span>}</div></form>
        {d.advisor_reviews.map((r) => <div key={r.id} className="card"><div className="row between"><span className="label">{fmt(r.at)}</span><span className="pill">{r.intervention_status}</span></div><p style={{ marginTop: 8 }}>{r.observation}</p><p className="mute small">→ {r.recommendation}</p>
          {r.intervention_required && r.intervention_status !== 'closed' && <div className="row" style={{ marginTop: 10 }}><button className="btn ghost sm" onClick={() => upd(r.id, 'in_progress')}>In progress</button><button className="btn sm" onClick={() => upd(r.id, 'closed')}>Close</button></div>}</div>)}</div>}
    </Page>
  )
}
