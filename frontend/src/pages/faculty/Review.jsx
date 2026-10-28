import { useState } from 'react'
import { api, errText } from '../../lib/api'
import { useGet } from '../../lib/hooks'
import { ErrorBox, Label, Loading, Page, fmt } from '../../ui/bits.jsx'

export default function Review() {
  const list = useGet('/faculty/assessments?status=published')
  const closed = useGet('/faculty/assessments?status=closed')
  const [aid, setAid] = useState('')
  const attempts = useGet(aid ? `/faculty/assessments/${aid}/attempts` : null, [aid])
  const [det, setDet] = useState(null)
  const [msg, setMsg] = useState('')
  const [scores, setScores] = useState({})
  const open = async (id) => { try { setDet((await api.get(`/faculty/attempts/${id}`)).data) } catch (e) { setMsg(errText(e)) } }
  const grade = async (row) => {
    try { await api.post(`/faculty/answers/${row.answer_id}/grade`, { score: Number(scores[row.answer_id]), comment: 'Reviewed by faculty' }); setMsg('Grade saved; record re-anchored with a new version.'); open(det.id); attempts.reload() } catch (e) { setMsg(errText(e)) }
  }
  const viol = async (v, status) => { try { await api.post(`/faculty/violations/${v.id}/review`, { review_status: status }); open(det.id); attempts.reload() } catch (e) { setMsg(errText(e)) } }
  const all = [...(list.data || []), ...(closed.data || [])]
  if (list.loading) return <Loading />
  return (
    <Page label="Review" title="Attempts & integrity">
      <select className="input" style={{ maxWidth: 420 }} value={aid} onChange={(e) => { setAid(e.target.value); setDet(null) }} aria-label="Assessment"><option value="">Choose an assessment…</option>{all.map((a) => <option key={a.id} value={a.id}>{a.title} ({a.status})</option>)}</select>
      <ErrorBox msg={msg} />
      {!det && attempts.data && <div className="table-wrap" style={{ marginTop: 20 }}><table><thead><tr><th>Student</th><th>Status</th><th>Score</th><th>Events</th><th>Flags</th><th /></tr></thead><tbody>{attempts.data.map((a) => (
        <tr key={a.id}><td>{a.student.name}<p className="small mute mono">{a.student.roll_number}</p></td><td>{a.status}</td><td>{a.percentage}%</td><td>{a.violations}</td>
          <td className="row">{a.needs_review && <span className="pill warn">answers to review</span>}{a.integrity_review_required && <span className="pill warn">integrity events</span>}</td><td><button className="btn ghost sm" onClick={() => open(a.id)}>Open</button></td></tr>))}</tbody></table></div>}
      {det && <div className="stack" style={{ marginTop: 20 }}>
        <div className="row between"><div><Label n={1}>Attempt #{det.id}</Label><div className="stat">{det.score}/{det.max}</div><p className="small mute">grade version {det.grade_version}</p></div><button className="btn ghost" onClick={() => setDet(null)}>← Back</button></div>
        {det.violations.length > 0 && <div className="card"><Label n={2}>Browser integrity events</Label><p className="small mute" style={{ margin: '6px 0 12px' }}>{det.integrity_note}</p>
          {det.violations.map((v) => <div key={v.id} className="row between" style={{ padding: '8px 0', borderTop: '1px solid var(--line)' }}><span>{v.type.replace('_', ' ')} · {fmt(v.at)}</span><span className="row"><span className="pill">{v.review_status}</span>{v.review_status === 'pending' && <><button className="btn ghost sm" onClick={() => viol(v, 'dismissed')}>Dismiss</button><button className="btn sm" onClick={() => viol(v, 'confirmed')}>Confirm concern</button></>}</span></div>)}</div>}
        {det.answers.map((r, i) => (
          <div key={i} className="card"><div className="row between"><span className="label">Q{i + 1} · {r.question.question_type} · {r.method || '—'}</span>{r.needs_review && <span className="pill warn">needs review</span>}</div>
            <p className="h3" style={{ margin: '8px 0' }}>{r.question.question_text}</p>
            <p className="small"><b>Answer:</b> {r.answer_text || '—'}</p>
            {r.voice && <p className="small mute">Spoken ({r.voice.duration?.toFixed(0)}s, {r.voice.engine}). Transcript above. Concepts found: {(r.voice.identified || []).join(', ') || '—'} · missing: {(r.voice.missing || []).join(', ') || '—'}</p>}
            {r.question.answer_key_text && <p className="small okmsg">Model: {r.question.answer_key_text}</p>}
            <p className="small mute">{r.feedback}</p>
            <div className="row" style={{ marginTop: 10 }}><span className="small">Score {r.override ?? r.score}/{r.question.marks}</span><input className="input" type="number" step="0.5" min="0" max={r.question.marks} style={{ maxWidth: 100 }} aria-label="New score" value={scores[r.answer_id] ?? ''} onChange={(e) => setScores({ ...scores, [r.answer_id]: e.target.value })} /><button className="btn sm" disabled={scores[r.answer_id] === undefined || scores[r.answer_id] === ''} onClick={() => grade(r)}>Override</button></div></div>))}</div>}
    </Page>
  )
}
