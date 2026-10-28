import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, errText } from '../../lib/api'
import { useGet } from '../../lib/hooks'
import { Empty, ErrorBox, fmt, Loading, Page } from '../../ui/bits.jsx'

export default function Assessments() {
  const { data, error, loading } = useGet('/student/assessments')
  const hist = useGet('/student/attempts')
  const subjects = useGet('/student/subjects')
  const [topic, setTopic] = useState('')
  const [count, setCount] = useState(5)
  const [msg, setMsg] = useState('')
  const nav = useNavigate()
  const practice = async () => {
    setMsg('')
    try { const { data: r } = await api.post('/student/practice', { topic_id: Number(topic), count }); nav(`/app/take/${r.assessment_id}`) }
    catch (e) { setMsg(errText(e)) }
  }
  if (loading) return <Loading />
  return (
    <Page label="Assessments" title="Assessments">
      <ErrorBox msg={error} />
      {data?.length ? <div className="grid g2">{data.map((a) => (
        <div key={a.id} className="card hover">
          <div className="row between"><span className="label">{a.subject_code} · {a.topic}</span><span className={`pill ${a.window === 'open' ? 'ok' : a.window === 'upcoming' ? 'warn' : ''}`}>{a.window}</span></div>
          <div className="h2" style={{ margin: '14px 0 6px' }}>{a.title}</div>
          <p className="mute small">{a.questions} questions · {a.total_marks} marks · {a.time_limit_minutes} min{a.has_voice ? ' · includes spoken answer' : ''}</p>
          <p className="mute small">Opens {fmt(a.start_time)} · closes {fmt(a.end_time)}</p>
          <div className="row" style={{ marginTop: 16 }}>
            {a.window === 'open' && a.attempts_used < a.max_attempts || a.attempt_status === 'in_progress' ? <Link className="btn accent sm" to={`/app/take/${a.id}`}>{a.attempt_status === 'in_progress' ? 'Resume' : 'Start'} →</Link> : null}
            {a.attempt_id && a.attempt_status !== 'in_progress' && <Link className="btn ghost sm" to={`/app/result/${a.attempt_id}`}>View result</Link>}
          </div>
        </div>))}</div> : <Empty title="No assessments yet">When faculty publish one for your class it will appear here.</Empty>}

      <div className="divider" />
      <div className="card"><div className="h2">Practice on your own</div><p className="mute">Private practice sets are built from approved material and match your current difficulty. They give a little XP, never affect your official record.</p>
        <div className="row" style={{ marginTop: 16 }}>
          <select className="input" style={{ maxWidth: 340 }} value={topic} onChange={(e) => setTopic(e.target.value)} aria-label="Topic">
            <option value="">Choose a topic…</option>
            {subjects.data?.flatMap((s) => s.units.flatMap((u) => u.topics.map((t) => <option key={t.id} value={t.id}>{s.code} · {t.title}</option>)))}
          </select>
          <input className="input" type="number" min="3" max="15" style={{ maxWidth: 90 }} value={count} onChange={(e) => setCount(+e.target.value)} aria-label="Number of questions" />
          <button className="btn sm" disabled={!topic} onClick={practice}>Build practice set</button></div>
        <ErrorBox msg={msg} /></div>

      {hist.data?.length > 0 && <><div className="divider" /><h2 className="h2" style={{ marginBottom: 16 }}>History</h2>
        <div className="table-wrap"><table><thead><tr><th>Assessment</th><th>Type</th><th>Status</th><th>Score</th><th /></tr></thead><tbody>{hist.data.map((h) => (
          <tr key={h.attempt_id}><td>{h.assessment}</td><td>{h.kind}</td><td>{h.status}</td><td>{h.percentage ?? 'after release'}{h.percentage != null && '%'}</td><td><Link className="mute" to={`/app/result/${h.attempt_id}`}>Open</Link></td></tr>))}</tbody></table></div></>}
    </Page>
  )
}
