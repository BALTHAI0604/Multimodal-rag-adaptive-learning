import { Link } from 'react-router-dom'
import { useGet } from '../../lib/hooks'
import { Bar, Empty, ErrorBox, Loading, Page, Reveal, Label } from '../../ui/bits.jsx'

export default function StudentHome() {
  const { data: d, error, loading } = useGet('/student/dashboard')
  if (loading) return <Loading />
  if (error) return <Page label="Today" title="Today"><ErrorBox msg={error} /></Page>
  const g = d.gamification
  return (
    <Page label="Today" title={`Hello, ${d.student.name.split(' ')[0]}.`}>
      <div className="grid g4">
        <Reveal className="card"><div className="label">Level {g.level}</div><div className="stat">{g.xp}<span className="mute small"> XP</span></div><p className="mute small">{g.level_title}{g.next_level_xp ? ` · next at ${g.next_level_xp}` : ''}</p></Reveal>
        <Reveal d={.06} className="card"><div className="label">Streak</div><div className="stat">{g.streak}<span className="mute small"> days</span></div><p className="mute small">Longest {g.longest_streak}</p></Reveal>
        <Reveal d={.12} className="card"><div className="label">Topics tracked</div><div className="stat">{d.mastery.topics.length}</div><p className="mute small">{d.mastery.strengths.length} strong · {d.mastery.weaknesses.length} to revisit</p></Reveal>
        <Reveal d={.18} className="card"><div className="label">Pending</div><div className="stat">{d.pending_assessments.length}</div><p className="mute small">assessments open now</p></Reveal>
      </div>

      <div className="divider" />
      <Label n={1}>Today’s topic</Label>
      {d.today_topic ? (
        <Reveal className="card hover" style={{ marginTop: 16 }} data-cursor="Ask">
          <div className="label">{d.today_topic.subject.code} · Unit {d.today_topic.unit_number}</div>
          <div className="h1" style={{ margin: '12px 0' }}>{d.today_topic.title}</div>
          <p className="mute">{d.today_topic.materials_count} approved material(s). Ask the companion or take the assessment when it opens.</p>
          <div className="row" style={{ marginTop: 16 }}><Link className="btn accent sm" to={`/app/companion?subject=${d.today_topic.subject.id}&topic=${d.today_topic.topic_id}`}>Ask the companion →</Link><Link className="btn ghost sm" to="/app/materials">Materials</Link></div>
        </Reveal>
      ) : <Empty title="No topic published for today">Your faculty has not selected today’s topic yet.</Empty>}

      {d.pending_assessments.length > 0 && <>
        <div className="divider" /><Label n={2}>Open assessments</Label>
        <div className="grid g2" style={{ marginTop: 16 }}>{d.pending_assessments.map((a) => (
          <div key={a.id} className="card"><div className="label">{a.subject_code}</div><div className="h2">{a.title}</div><p className="mute small">{a.questions} questions · {a.time_limit_minutes} min</p><Link to={`/app/take/${a.id}`} className="btn accent sm" style={{ marginTop: 14 }}>{a.attempt_status === 'in_progress' ? 'Resume' : 'Start'} →</Link></div>))}</div></>}

      <div className="divider" /><Label n={3}>Your mastery</Label>
      {d.empty ? <Empty title="Nothing to show yet">Mastery appears after your first completed assessment — it is never pre-filled.</Empty> : (
        <div className="grid g2" style={{ marginTop: 16 }}>{d.mastery.topics.map((t) => (
          <div key={t.topic_id} className="card"><div className="row between"><span className="h3">{t.topic_name}</span><span className="pill">{t.trend.replace('_', ' ')}</span></div>
            <p className="label" style={{ margin: '6px 0 14px' }}>{t.subject_code} · next difficulty {t.current_difficulty}</p><Bar value={t.mastery_score} /><p className="stat" style={{ fontSize: 28, marginTop: 10 }}>{Math.round(t.mastery_score)}%</p></div>))}</div>)}

      {d.recommendations.length > 0 && <>
        <div className="divider" /><Label n={4}>Recommended next</Label>
        <div className="stack" style={{ marginTop: 16 }}>{d.recommendations.map((r) => (
          <div key={r.id} className="card"><div className="row between"><span className="h3">{r.title}</span><span className={`pill ${r.priority === 'high' ? 'bad' : ''}`}>{r.priority}</span></div><p className="mute" style={{ marginTop: 6 }}>{r.reason}</p><p className="small" style={{ marginTop: 6 }}>{r.description}</p></div>))}</div></>}
    </Page>
  )
}
