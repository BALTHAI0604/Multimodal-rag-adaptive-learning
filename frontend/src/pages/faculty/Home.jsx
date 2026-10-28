import { Link } from 'react-router-dom'
import { useState } from 'react'
import { useGet } from '../../lib/hooks'
import { Bar, Empty, ErrorBox, Label, Loading, Page } from '../../ui/bits.jsx'

export default function FacultyHome() {
  const { data: subs, loading } = useGet('/faculty/subjects')
  const [sid, setSid] = useState('')
  const subject = sid || subs?.[0]?.id
  const an = useGet(subject ? `/faculty/analytics?subject_id=${subject}` : null, [subject])
  if (loading) return <Loading />
  if (!subs?.length) return <Page label="Overview" title="Overview"><Empty title="No subjects assigned">Ask your administrator to assign you to a subject.</Empty></Page>
  const a = an.data
  return (
    <Page label="Overview" title="Your classes" actions={<select className="input" style={{ maxWidth: 300 }} value={subject} onChange={(e) => setSid(e.target.value)} aria-label="Subject">{subs.map((s) => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}</select>}>
      <ErrorBox msg={an.error} />
      {a && <>
        <div className="grid g4">
          <div className="card"><div className="label">Students</div><div className="stat">{a.students}</div></div>
          <div className="card"><div className="label">Completed attempts</div><div className="stat">{a.attempts}</div></div>
          <div className="card"><div className="label">Average score</div><div className="stat">{a.avg_percentage ?? '—'}{a.avg_percentage != null && '%'}</div></div>
          <div className="card"><div className="label">Needs your review</div><div className="stat">{a.pending_review + a.pending_integrity}</div><Link to="/app/review" className="small mute">Open review →</Link></div>
        </div>
        <div className="divider" /><Label n={1}>Topics</Label>
        <div className="table-wrap"><table><thead><tr><th>Unit</th><th>Topic</th><th>Students</th><th>Avg mastery</th><th>Struggling</th></tr></thead><tbody>{a.topics.map((t) => (
          <tr key={t.topic_id}><td>{t.unit}</td><td>{t.title}</td><td>{t.students}</td><td style={{ minWidth: 160 }}>{t.avg_mastery != null ? <><Bar value={t.avg_mastery} /><span className="small">{t.avg_mastery}%</span></> : <span className="mute">no data</span>}</td><td>{t.struggling}</td></tr>))}</tbody></table></div>
        <div className="divider" /><Label n={2}>Common misconceptions</Label>
        {a.common_misconceptions.length ? a.common_misconceptions.map((m, i) => <div key={i} className="index-row"><span className="label">{m.occurrences}×</span><div><p style={{ color: 'var(--text)', fontSize: 18 }}>{m.description}</p></div><span /></div>) : <p className="mute" style={{ marginTop: 12 }}>None detected yet.</p>}
        <div className="divider" /><Label n={3}>Roster</Label>
        <div className="table-wrap"><table><thead><tr><th>ID</th><th>Name</th><th>Attempts</th><th>Avg score</th><th>Mastery</th><th /></tr></thead><tbody>{a.roster.map((r) => (
          <tr key={r.student_id}><td className="mono">{r.roll_number}</td><td>{r.name}</td><td>{r.attempts}</td><td>{r.avg_percentage ?? '—'}</td><td>{r.avg_mastery ?? '—'}</td><td>{r.needs_attention && <span className="pill bad">needs attention</span>}</td></tr>))}</tbody></table></div></>}
    </Page>
  )
}
