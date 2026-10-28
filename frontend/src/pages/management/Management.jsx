import { useState } from 'react'
import { Bar as B, Empty, ErrorBox, Label, Loading, Page } from '../../ui/bits.jsx'
import { api, errText } from '../../lib/api'
import { useGet } from '../../lib/hooks'
import { Bar as BarChart, BarChart as RBar, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export default function Management() {
  const [dept, setDept] = useState('')
  const [sem, setSem] = useState('')
  const q = `/management/analytics?${dept ? `department=${dept}&` : ''}${sem ? `semester=${sem}` : ''}`
  const { data: d, loading, error } = useGet(q, [dept, sem])
  const [msg, setMsg] = useState('')
  const csv = async () => {
    try { const r = await api.get(q.replace('analytics', 'export.csv'), { responseType: 'blob' }); const a = document.createElement('a'); a.href = URL.createObjectURL(r.data); a.download = 'institution-summary.csv'; a.click() } catch (e) { setMsg(errText(e)) }
  }
  if (loading && !d) return <Loading />
  const k = d?.kpis
  const chart = (d?.departments || []).filter((x) => !x.suppressed).map((x) => ({ name: x.department, mastery: x.avg_mastery || 0, score: x.avg_assessment_percentage || 0 }))
  return (
    <Page label="Management" title="Institutional insight" actions={<div className="row"><input className="input" style={{ maxWidth: 120 }} placeholder="Dept" value={dept} onChange={(e) => setDept(e.target.value)} aria-label="Department filter" /><input className="input" style={{ maxWidth: 90 }} type="number" min="1" max="12" placeholder="Sem" value={sem} onChange={(e) => setSem(e.target.value)} aria-label="Semester filter" /><button className="btn ghost sm" onClick={csv}>Export CSV</button></div>}>
      <ErrorBox msg={error || msg} />
      {k && <>
        <div className="grid g4">{[['Students', k.students], ['With assessments', k.students_with_assessments], ['Assessments completed', k.assessments_completed], ['Avg score', k.avg_assessment_percentage != null ? `${k.avg_assessment_percentage}%` : '—'], ['Avg mastery', k.avg_mastery != null ? `${k.avg_mastery}%` : '—'], ['Materials indexed', k.materials_indexed]].map(([l, v]) => <div key={l} className="card"><div className="label">{l}</div><div className="stat">{v}</div></div>)}</div>
        <p className="pill" style={{ margin: '20px 0' }}>{d.privacy_note}</p>
        <Label n={1}>Departments</Label>
        {chart.length ? <div className="card" style={{ height: 300, marginTop: 14 }}><ResponsiveContainer><RBar data={chart}><CartesianGrid stroke="var(--line)" vertical={false} /><XAxis dataKey="name" stroke="var(--mute)" /><YAxis stroke="var(--mute)" domain={[0, 100]} /><Tooltip contentStyle={{ background: 'var(--bg2)', border: '1px solid var(--line)' }} /><BarChart dataKey="mastery" name="Avg mastery" fill="var(--accent)" radius={[6, 6, 0, 0]} /><BarChart dataKey="score" name="Avg score" fill="var(--text)" radius={[6, 6, 0, 0]} /></RBar></ResponsiveContainer></div> : <Empty title="Not enough data">Groups with fewer than 5 students are hidden to protect individuals.</Empty>}
        <div className="divider" /><Label n={2}>Subjects</Label>
        <div className="table-wrap"><table><thead><tr><th>Subject</th><th>Students</th><th>Avg mastery</th></tr></thead><tbody>{d.subjects.map((s) => <tr key={s.code}><td>{s.code} · {s.name}</td><td>{s.students}</td><td>{s.suppressed ? 'suppressed' : <><B value={s.avg_mastery} /><span className="small">{s.avg_mastery}%</span></>}</td></tr>)}</tbody></table></div>
        <div className="divider" /><Label n={3}>Hardest topics</Label>
        {d.difficult_topics.length ? d.difficult_topics.map((t, i) => <div key={i} className="index-row"><span className="label">{t.subject}</span><h3 style={{ fontSize: 24 }}>{t.topic}</h3><span className="pill bad">{t.struggling_pct}% struggling</span></div>) : <p className="mute" style={{ marginTop: 12 }}>No topic has enough students yet.</p>}</>}
    </Page>
  )
}
