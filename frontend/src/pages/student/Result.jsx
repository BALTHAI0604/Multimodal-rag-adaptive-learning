import { Link, useParams } from 'react-router-dom'
import { useGet } from '../../lib/hooks'
import { ErrorBox, Label, Loading, Page, fmt } from '../../ui/bits.jsx'
import { useState } from 'react'
import { api, errText } from '../../lib/api'

export default function Result() {
  const { attemptId } = useParams()
  const { data: r, error, loading } = useGet(`/student/attempts/${attemptId}/result`)
  const [v, setV] = useState(null)
  if (loading) return <Loading />
  if (error) return <Page label="Result" title="Result"><ErrorBox msg={error} /></Page>
  const verify = async () => { try { setV((await api.post('/blockchain/verify', { record_ref: r.ledger.record_ref })).data) } catch (e) { setV({ message: errText(e) }) } }
  return (
    <Page label="Result" title={r.assessment}>
      <div className="grid g3">
        <div className="card"><div className="label">Score</div><div className="stat">{r.percentage != null ? `${r.percentage}%` : '—'}</div><p className="mute small">{r.score != null ? `${r.score} / ${r.max_score}` : r.message}</p></div>
        <div className="card"><div className="label">Status</div><div className="h2" style={{ marginTop: 12 }}>{r.status.replace('_', ' ')}</div>{r.needs_review && <p className="pill warn" style={{ marginTop: 10 }}>Awaiting faculty review</p>}</div>
        <div className="card"><div className="label">Record integrity</div>
          {r.ledger ? <><div className="h3" style={{ margin: '12px 0' }}>{r.ledger.status === 'anchored' ? 'Anchored on chain' : 'Not anchored yet'}</div>
            <button className="btn ghost sm" onClick={verify}>Verify</button>{v && <p className="small" style={{ marginTop: 10 }}><b>{v.result}</b> — {v.message}</p>}</> : <p className="mute">No ledger record.</p>}</div>
      </div>
      <div className="divider" />
      {!r.answers_released ? <div className="card"><Label n={1}>Answers</Label><p className="mute" style={{ marginTop: 8 }}>Correct answers and explanations are hidden until {r.answers_release_at ? fmt(r.answers_release_at) : 'faculty release them'}.</p></div> : (
        <div className="stack">{r.questions?.map((q, i) => (
          <div key={q.id} className="card"><div className="row between"><span className="label">Q{i + 1} · {q.type.replace('_', ' ')}</span><span className="pill">{q.score}/{q.marks}</span></div>
            <p className="h3" style={{ margin: '10px 0' }}>{q.question_text}</p>
            <p className="small mute">Your answer: {q.your_answer || '—'}</p>
            {q.correct_options && <p className="small okmsg">Correct: {q.correct_options.join(' / ')}</p>}
            {q.model_answer && <p className="small okmsg">Model answer: {q.model_answer}</p>}
            {q.accepted_answers && <p className="small okmsg">Accepted: {q.accepted_answers.join(', ')}</p>}
            {q.correct_value != null && <p className="small okmsg">Correct value: {q.correct_value}</p>}
            {q.feedback && <p className="small">{q.feedback}</p>}{q.explanation && <p className="small mute">{q.explanation}</p>}{q.faculty_comment && <p className="small">Faculty: {q.faculty_comment}</p>}</div>))}</div>)}
      <div className="row" style={{ marginTop: 24 }}><Link className="btn ghost" to="/app">Back to today</Link></div>
    </Page>
  )
}
