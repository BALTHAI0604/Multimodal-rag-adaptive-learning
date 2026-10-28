import { useState } from 'react'
import { api, errText } from '../../lib/api'
import { useGet } from '../../lib/hooks'
import { ErrorBox, Label, Loading, Page, fmt } from '../../ui/bits.jsx'

const TYPES = ['notes', 'slides', 'syllabus', 'reference', 'question_bank', 'lecture_media', 'scan']

export default function Curriculum() {
  const { data: subs, loading, reload } = useGet('/faculty/subjects')
  const [sid, setSid] = useState('')
  const subject = subs?.find((s) => s.id === Number(sid)) || subs?.[0]
  const mats = useGet(subject ? `/faculty/materials?subject_id=${subject.id}` : null, [subject?.id])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ unit_id: '', topic_id: '', title: '', material_type: 'notes', class_id: '' })
  const [file, setFile] = useState(null)
  const [newUnit, setNewUnit] = useState({ unit_number: '', title: '' })
  const [newTopic, setNewTopic] = useState({ unit_id: '', title: '' })

  const wrap = async (fn, ok) => { setMsg(''); setBusy(true); try { await fn(); setMsg(ok) ; reload(); mats.reload() } catch (e) { setMsg(errText(e)) } finally { setBusy(false) } }
  if (loading) return <Loading />
  if (!subject) return <Page label="Curriculum" title="Curriculum"><p className="mute">No subjects assigned.</p></Page>
  const topics = subject.units.find((u) => u.id === Number(form.unit_id))?.topics || []
  const upload = (e) => {
    e.preventDefault()
    const fd = new FormData(); Object.entries({ subject_id: subject.id, ...form }).forEach(([k, v]) => v !== '' && fd.append(k, v)); fd.append('file', file)
    wrap(async () => { const { data } = await api.post('/faculty/materials/upload', fd); if (data.status === 'error') throw { response: { data: { detail: data.error_message || 'Processing failed' } } }; setFile(null) }, 'Uploaded and indexed.')
  }
  return (
    <Page label="Curriculum" title="Curriculum & material" actions={<select className="input" style={{ maxWidth: 300 }} value={subject.id} onChange={(e) => setSid(e.target.value)} aria-label="Subject">{subs.map((s) => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}</select>}>
      <p className={msg.includes('indexed') || msg.includes('Saved') ? 'okmsg' : 'err'} role="status">{msg}</p>
      <Label n={1}>Units & topics — pick today’s topic</Label>
      {subject.units.map((u) => (
        <div key={u.id} className="card" style={{ marginTop: 14 }}>
          <div className="h3">Unit {u.unit_number}: {u.title}</div>
          {u.topics.map((t) => (
            <div key={t.id} className="index-row" style={{ gridTemplateColumns: '1fr auto auto', padding: '14px 0' }}>
              <div><span style={{ fontSize: 18 }}>{t.title}</span><p className="small mute">{t.materials_count} material(s)</p></div>
              {t.is_today_topic ? <span className="pill acc">today’s topic</span> : <span />}
              <button className={`btn sm ${t.is_today_topic ? 'ghost' : 'accent'}`} disabled={busy} onClick={() => wrap(() => api.post('/faculty/today-topic', { topic_id: t.id, class_id: subject.classes[0]?.id ?? null, is_today: !t.is_today_topic }), 'Saved.')}>{t.is_today_topic ? 'Unset' : 'Set as today'}</button></div>))}
          <div className="row" style={{ marginTop: 10 }}><input className="input" style={{ maxWidth: 300 }} placeholder="New topic title" value={newTopic.unit_id === u.id ? newTopic.title : ''} onChange={(e) => setNewTopic({ unit_id: u.id, title: e.target.value })} />
            <button className="btn ghost sm" disabled={newTopic.unit_id !== u.id || newTopic.title.length < 2} onClick={() => wrap(() => api.post(`/faculty/units/${u.id}/topics`, { title: newTopic.title }), 'Saved.')}>Add topic</button></div></div>))}
      <div className="row" style={{ marginTop: 14 }}>
        <input className="input" style={{ maxWidth: 90 }} type="number" placeholder="No." value={newUnit.unit_number} onChange={(e) => setNewUnit({ ...newUnit, unit_number: e.target.value })} aria-label="Unit number" />
        <input className="input" style={{ maxWidth: 300 }} placeholder="New unit title" value={newUnit.title} onChange={(e) => setNewUnit({ ...newUnit, title: e.target.value })} />
        <button className="btn ghost sm" disabled={!newUnit.unit_number || newUnit.title.length < 2} onClick={() => wrap(() => api.post(`/faculty/subjects/${subject.id}/units`, { unit_number: +newUnit.unit_number, title: newUnit.title }), 'Saved.')}>Add unit</button></div>

      <div className="divider" /><Label n={2}>Upload material</Label>
      <form className="card stack" style={{ marginTop: 14 }} onSubmit={upload}>
        <div className="grid g3">
          <div className="field"><label htmlFor="u">Unit</label><select id="u" className="input" required value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value, topic_id: '' })}><option value="">Select…</option>{subject.units.map((u) => <option key={u.id} value={u.id}>Unit {u.unit_number}: {u.title}</option>)}</select></div>
          <div className="field"><label htmlFor="t">Topic</label><select id="t" className="input" required value={form.topic_id} onChange={(e) => setForm({ ...form, topic_id: e.target.value })}><option value="">Select…</option>{topics.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}</select></div>
          <div className="field"><label htmlFor="c">Visible to</label><select id="c" className="input" value={form.class_id} onChange={(e) => setForm({ ...form, class_id: e.target.value })}><option value="">All my classes</option>{subject.classes.map((c) => <option key={c.id} value={c.id}>Class {c.name}</option>)}</select></div>
          <div className="field"><label htmlFor="ti">Title</label><input id="ti" className="input" required minLength={2} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
          <div className="field"><label htmlFor="mt">Type</label><select id="mt" className="input" value={form.material_type} onChange={(e) => setForm({ ...form, material_type: e.target.value })}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select></div>
          <div className="field"><label htmlFor="f">File</label><input id="f" className="input" type="file" required accept=".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg,.webp,.tif,.tiff,.mp3,.wav,.m4a,.ogg,.mp4,.mkv,.webm,.mov" onChange={(e) => setFile(e.target.files[0])} /></div></div>
        <p className="small mute">PDF, DOCX, PPTX, TXT, MD, images (OCR) and audio/video (transcribed). Text is extracted and indexed on the server; no model is retrained.</p>
        <div><button className="btn accent" disabled={busy || !file || !form.topic_id || form.title.length < 2}>{busy ? 'Processing…' : 'Upload & index'}</button></div></form>

      <div className="divider" /><Label n={3}>Library</Label>
      <ErrorBox msg={mats.error} />
      <div className="table-wrap"><table><thead><tr><th>Title</th><th>Status</th><th>Chunks</th><th>Added</th><th>Visible to students</th><th /></tr></thead><tbody>{mats.data?.map((m) => (
        <tr key={m.id}><td>{m.title}<p className="small mute">{m.file_name}</p>{m.error_message && <p className="small err">{m.error_message}</p>}</td>
          <td><span className={`pill ${m.status === 'indexed' ? 'ok' : m.status === 'error' ? 'bad' : 'warn'}`}>{m.status}</span></td><td>{m.chunks}</td><td>{fmt(m.created_at)}</td>
          <td><input type="checkbox" checked={m.is_approved} aria-label="Approved for students" onChange={(e) => wrap(() => api.patch(`/faculty/materials/${m.id}`, { is_approved: e.target.checked }), 'Saved.')} /></td>
          <td className="row"><button className="btn ghost sm" onClick={() => wrap(() => api.post(`/faculty/materials/${m.id}/reprocess`), 'Reprocessed.')}>Reprocess</button><button className="btn danger sm" onClick={() => confirm('Delete this material and its index?') && wrap(() => api.delete(`/faculty/materials/${m.id}`), 'Saved.')}>Delete</button></td></tr>))}</tbody></table></div>
    </Page>
  )
}
