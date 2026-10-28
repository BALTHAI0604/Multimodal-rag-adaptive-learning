import { useState } from 'react'
import { api, errText } from '../../lib/api'
import { useGet } from '../../lib/hooks'
import { CopyButton, ErrorBox, Label, Loading, Modal, Page, fmt } from '../../ui/bits.jsx'

export default function Admin() {
  const [tab, setTab] = useState('users')
  const users = useGet('/admin/users?limit=300')
  const st = useGet('/admin/structure')
  const sys = useGet('/admin/system')
  const logs = useGet('/admin/audit-logs?limit=100')
  const [msg, setMsg] = useState('')
  const [secret, setSecret] = useState(null)
  const [note, setNote] = useState('')
  const [u, setU] = useState({ email: '', full_name: '', role: 'student', roll_number: '', employee_id: '', department: '', semester: 1, class_id: '' })
  const [dep, setDep] = useState({ code: '', name: '' })
  const [cls, setCls] = useState({ name: '', department_code: '', semester: 1, advisor_user_id: '' })
  const [sub, setSub] = useState({ code: '', name: '', department: '', semester: 1 })
  const [asg, setAsg] = useState({ subject_id: '', faculty_id: '', class_id: '' })
  const act = async (fn, ok = 'Saved.') => { setMsg(''); setNote(''); try { const r = await fn(); setNote(ok); users.reload(); st.reload(); return r } catch (e) { setMsg(errText(e)) } }
  const create = (e) => {
    e.preventDefault()
    const body = { email: u.email, full_name: u.full_name, role: u.role }
    if (u.role === 'student') body.student = { roll_number: u.roll_number, department: u.department, semester: +u.semester, class_id: u.class_id ? +u.class_id : null }
    if (u.role === 'faculty') body.faculty = { employee_id: u.employee_id, department: u.department }
    act(async () => { const { data } = await api.post('/admin/users', body); if (data.temporary_password) setSecret({ who: body.email, pw: data.temporary_password, title: 'Account created' }) }, 'Account created.')
  }
  if (users.loading) return <Loading />
  const T = ({ id, children }) => <button role="tab" aria-selected={tab === id} className={`pill ${tab === id ? 'acc' : ''}`} onClick={() => setTab(id)}>{children}</button>
  return (
    <Page label="Administration" title="Admin">
      <div className="tabs" role="tablist" aria-label="Administration sections"><T id="users">Users</T><T id="structure">Structure</T><T id="system">System</T><T id="audit">Audit log</T></div>
      {secret && <Modal title={secret.title} onClose={() => setSecret(null)}>
        <p className="mute">One-time password for <b>{secret.who}</b>. It is shown only now and is never stored in readable form. Share it privately; the user should change it after signing in.</p>
        <div className="secret">{secret.pw}</div>
        <div className="row"><CopyButton text={secret.pw} label="Copy password" /><button className="btn accent sm" onClick={() => setSecret(null)}>Done</button></div>
      </Modal>}
      <ErrorBox msg={msg} />{note && <p className="okmsg" role="status">{note}</p>}

      {tab === 'users' && <>
        <form className="card stack" onSubmit={create}><Label n={1}>Create account</Label><p className="small mute">There is no self sign-up. In Firebase mode the person receives a password-setup e-mail; in local development mode a one-time password is shown here.</p>
          <div className="grid g3">
            <div className="field"><label htmlFor="e">E-mail</label><input id="e" required type="email" className="input" value={u.email} onChange={(e) => setU({ ...u, email: e.target.value })} /></div>
            <div className="field"><label htmlFor="n">Full name</label><input id="n" required minLength={2} className="input" value={u.full_name} onChange={(e) => setU({ ...u, full_name: e.target.value })} /></div>
            <div className="field"><label htmlFor="r">Role</label><select id="r" className="input" value={u.role} onChange={(e) => setU({ ...u, role: e.target.value })}>{['student', 'faculty', 'advisor', 'management', 'admin'].map((r) => <option key={r}>{r}</option>)}</select></div>
            {u.role === 'student' && <><div className="field"><label htmlFor="ro">Student ID</label><input id="ro" required className="input" value={u.roll_number} onChange={(e) => setU({ ...u, roll_number: e.target.value })} /></div>
              <div className="field"><label htmlFor="cl">Class</label><select id="cl" className="input" value={u.class_id} onChange={(e) => setU({ ...u, class_id: e.target.value })}><option value="">None</option>{st.data?.classes.map((c) => <option key={c.id} value={c.id}>{c.department_code} {c.name}</option>)}</select></div>
              <div className="field"><label htmlFor="se">Semester</label><input id="se" type="number" min="1" max="12" className="input" value={u.semester} onChange={(e) => setU({ ...u, semester: e.target.value })} /></div></>}
            {u.role === 'faculty' && <div className="field"><label htmlFor="em">Employee ID</label><input id="em" required className="input" value={u.employee_id} onChange={(e) => setU({ ...u, employee_id: e.target.value })} /></div>}
            {['student', 'faculty'].includes(u.role) && <div className="field"><label htmlFor="de">Department</label><input id="de" required className="input" value={u.department} onChange={(e) => setU({ ...u, department: e.target.value })} /></div>}</div>
          <div><button className="btn accent">Create</button></div></form>
        <div className="divider" />
        <div className="table-wrap"><table><thead><tr><th>Name</th><th>E-mail</th><th>Role</th><th>ID</th><th>Status</th><th /></tr></thead><tbody>{users.data?.map((x) => (
          <tr key={x.id}><td>{x.full_name}</td><td>{x.email}</td><td>{x.role}</td><td className="mono">{x.student?.roll_number || x.faculty?.employee_id || ''}</td><td><span className={`pill ${x.is_active ? 'ok' : 'bad'}`}>{x.is_active ? 'active' : 'disabled'}</span></td>
            <td className="row"><button className="btn ghost sm" onClick={() => act(() => api.patch(`/admin/users/${x.id}`, { is_active: !x.is_active }))}>{x.is_active ? 'Disable' : 'Enable'}</button>
              <button className="btn ghost sm" onClick={() => act(async () => { const { data } = await api.post(`/admin/users/${x.id}/reset-password`); if (data.temporary_password) setSecret({ who: x.email, pw: data.temporary_password, title: 'Password reset' }) }, 'Password reset.')}>Reset password</button></td></tr>))}</tbody></table></div></>}

      {tab === 'structure' && <div className="stack">
        <div className="grid g2">
          <form className="card stack-s" onSubmit={(e) => { e.preventDefault(); act(() => api.post('/admin/departments', dep)) }}><Label n={1}>Department</Label><input className="input" placeholder="Code" required value={dep.code} onChange={(e) => setDep({ ...dep, code: e.target.value })} /><input className="input" placeholder="Name" required value={dep.name} onChange={(e) => setDep({ ...dep, name: e.target.value })} /><button className="btn sm">Add</button></form>
          <form className="card stack-s" onSubmit={(e) => { e.preventDefault(); act(() => api.post('/admin/classes', { ...cls, semester: +cls.semester, advisor_user_id: cls.advisor_user_id ? +cls.advisor_user_id : null })) }}><Label n={2}>Class</Label><input className="input" placeholder="Name (e.g. A)" required value={cls.name} onChange={(e) => setCls({ ...cls, name: e.target.value })} /><input className="input" placeholder="Department code" required value={cls.department_code} onChange={(e) => setCls({ ...cls, department_code: e.target.value })} /><input className="input" type="number" min="1" max="12" value={cls.semester} onChange={(e) => setCls({ ...cls, semester: e.target.value })} aria-label="Semester" />
            <select className="input" value={cls.advisor_user_id} onChange={(e) => setCls({ ...cls, advisor_user_id: e.target.value })} aria-label="Advisor"><option value="">No advisor</option>{st.data?.advisors.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select><button className="btn sm">Add</button></form>
          <form className="card stack-s" onSubmit={(e) => { e.preventDefault(); act(() => api.post('/admin/subjects', { ...sub, semester: +sub.semester })) }}><Label n={3}>Subject</Label><input className="input" placeholder="Code" required value={sub.code} onChange={(e) => setSub({ ...sub, code: e.target.value })} /><input className="input" placeholder="Name" required value={sub.name} onChange={(e) => setSub({ ...sub, name: e.target.value })} /><input className="input" placeholder="Department" required value={sub.department} onChange={(e) => setSub({ ...sub, department: e.target.value })} /><input className="input" type="number" min="1" max="12" value={sub.semester} onChange={(e) => setSub({ ...sub, semester: e.target.value })} aria-label="Semester" /><button className="btn sm">Add</button></form>
          <form className="card stack-s" onSubmit={(e) => { e.preventDefault(); act(() => api.post('/admin/assignments', { subject_id: +asg.subject_id, faculty_id: +asg.faculty_id, class_id: asg.class_id ? +asg.class_id : null })) }}><Label n={4}>Assign faculty to subject</Label>
            <select className="input" required value={asg.subject_id} onChange={(e) => setAsg({ ...asg, subject_id: e.target.value })} aria-label="Subject"><option value="">Subject…</option>{st.data?.subjects.map((s) => <option key={s.id} value={s.id}>{s.code}</option>)}</select>
            <select className="input" required value={asg.faculty_id} onChange={(e) => setAsg({ ...asg, faculty_id: e.target.value })} aria-label="Faculty"><option value="">Faculty…</option>{st.data?.faculty.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}</select>
            <select className="input" value={asg.class_id} onChange={(e) => setAsg({ ...asg, class_id: e.target.value })} aria-label="Class"><option value="">All classes</option>{st.data?.classes.map((c) => <option key={c.id} value={c.id}>{c.department_code} {c.name}</option>)}</select><button className="btn sm">Assign</button></form></div>
        <div className="table-wrap"><table><thead><tr><th>Subject</th><th>Dept</th><th>Sem</th></tr></thead><tbody>{st.data?.subjects.map((s) => <tr key={s.id}><td>{s.code} · {s.name}</td><td>{s.department}</td><td>{s.semester}</td></tr>)}</tbody></table></div></div>}

      {tab === 'system' && sys.data && <div className="grid g3">
        {[['Auth mode', sys.data.auth_mode], ['Environment', sys.data.environment], ['LLM (Ollama)', sys.data.llm.available ? `ready · ${sys.data.llm.model || ''}` : `offline — ${sys.data.llm.error || ''}`],
          ['Embeddings', `${sys.data.embedding.model}${sys.data.embedding.backend === 'hashing' ? ' (lexical fallback, not semantic)' : ''}`], ['Vector index', `${sys.data.vector_index.backend} · ${sys.data.vector_index.chunks} chunks`],
          ['Speech-to-text', sys.data.speech_to_text.available ? `faster-whisper (${sys.data.speech_to_text.model})` : 'not installed'], ['Text-to-speech', Object.entries(sys.data.text_to_speech).map(([k, v]) => `${k}:${v ? '✓' : '✗'}`).join(' ')],
          ['OCR', sys.data.ocr ? 'available' : 'not installed'], ['Blockchain', sys.data.blockchain.connected ? `connected · ${sys.data.blockchain.total_anchored_on_chain} anchored` : `not connected — ${sys.data.blockchain.error}`]]
          .map(([k, v]) => <div key={k} className="card"><div className="label">{k}</div><p style={{ marginTop: 8 }}>{String(v)}</p></div>)}
        <div className="card row"><button className="btn ghost sm" onClick={() => act(() => api.post('/admin/rag/reindex'), 'Reindexed.')}>Rebuild vector index</button><button className="btn ghost sm" onClick={() => act(() => api.post('/admin/maintenance/purge'), 'Retention applied.')}>Apply retention</button><button className="btn ghost sm" onClick={() => act(() => api.post('/blockchain/retry-pending'), 'Retried.')}>Retry pending anchors</button></div></div>}

      {tab === 'audit' && <div className="table-wrap"><table><thead><tr><th>Time</th><th>Action</th><th>Role</th><th>Resource</th><th>Outcome</th></tr></thead><tbody>{logs.data?.map((l) => <tr key={l.id}><td className="small">{fmt(l.time + 'Z')}</td><td className="mono small">{l.action}</td><td>{l.role || '—'}</td><td>{l.resource} {l.resource_id}</td><td><span className={`pill ${l.outcome === 'success' ? 'ok' : 'bad'}`}>{l.outcome}</span></td></tr>)}</tbody></table></div>}
    </Page>
  )
}
