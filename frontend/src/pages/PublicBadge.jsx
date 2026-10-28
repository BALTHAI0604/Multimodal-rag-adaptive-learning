import { useParams, Link } from 'react-router-dom'
import { useGet } from '../lib/hooks'
import { Label, Loading } from '../ui/bits.jsx'

export default function PublicBadge() {
  const { token } = useParams()
  const { data, error, loading } = useGet(`/public/badge/${token}`)
  if (loading) return <Loading />
  return (
    <main className="wrap" style={{ minHeight: '100svh', display: 'grid', alignContent: 'center', gap: 24 }} id="main">
      <Label n={1}>Achievement</Label>
      {error ? <h1 className="h1">This link is no longer available.</h1> : (
        <>
          <h1 className="display" style={{ fontSize: 'clamp(48px,10vw,150px)' }}>{data.badge.name}</h1>
          <p className="lead">{data.badge.description}</p>
          <p className="label">Earned by {data.holder} · {new Date(data.awarded_at).toLocaleDateString()} · Criteria: {data.badge.criteria}</p>
        </>
      )}
      <Link to="/" className="label">← Home</Link>
    </main>
  )
}
