import { api } from '../../lib/api'
import { useGet } from '../../lib/hooks'
import { Empty, Loading, Page } from '../../ui/bits.jsx'

export default function Materials() {
  const { data, loading } = useGet('/student/materials')
  const subs = useGet('/student/subjects')
  const download = async (m) => {
    const r = await api.get(`/student/materials/${m.id}/download`, { responseType: 'blob' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(r.data); a.download = m.file_name || m.title; a.click(); URL.revokeObjectURL(a.href)
  }
  if (loading) return <Loading />
  const topicName = (id) => subs.data?.flatMap((s) => s.units.flatMap((u) => u.topics)).find((t) => t.id === id)?.title
  return (
    <Page label="Materials" title="Course material">
      {data?.length ? data.map((m, i) => (
        <div key={m.id} className="index-row"><span className="label">{String(i + 1).padStart(2, '0')}</span><div><h3 style={{ fontSize: 28 }}>{m.title}</h3><p>{topicName(m.topic_id)} · {m.type}</p></div><button className="btn ghost sm" onClick={() => download(m)}>Download</button></div>))
        : <Empty title="No material yet">Approved and indexed faculty material for your subjects will list here.</Empty>}
    </Page>
  )
}
