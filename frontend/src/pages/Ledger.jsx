import { useState } from 'react'
import { api, errText } from '../lib/api'
import { useGet } from '../lib/hooks'
import { Empty, ErrorBox, Label, Loading, Page, fmt } from '../ui/bits.jsx'

const TONE = { VERIFIED: 'ok', MISMATCH: 'bad', NOT_ANCHORED: 'warn', CHAIN_UNAVAILABLE: 'warn', CHAIN_RESET: 'warn', SUPERSEDED: '', REVOKED: 'bad' }
export default function Ledger() {
  const recs = useGet('/blockchain/records')
  const stat = useGet('/blockchain/status')
  const [res, setRes] = useState({})
  const [msg, setMsg] = useState('')
  const verify = async (ref) => { try { setRes({ ...res, [ref]: (await api.post('/blockchain/verify', { record_ref: ref })).data }); recs.reload() } catch (e) { setMsg(errText(e)) } }
  if (recs.loading) return <Loading />
  return (
    <Page label="Integrity" title="Record integrity">
      <div className="card"><p className="mute">Only an opaque record id and a keyed SHA-256 hash are written to the blockchain — never marks, names, e-mails or transcripts. “Verify” recomputes the hash from the current database and compares it with the chain.</p>
        <p style={{ marginTop: 10 }}><span className={`pill ${stat.data?.connected ? 'ok' : 'warn'}`}>{stat.data?.connected ? 'chain connected' : 'chain not connected — records stay pending'}</span></p></div>
      <ErrorBox msg={msg || recs.error} />
      <div className="divider" /><Label n={1}>Records</Label>
      {recs.data?.length ? <div className="table-wrap"><table><thead><tr><th>Record</th><th>v</th><th>Status</th><th>Block</th><th>Created</th><th>Last check</th><th /></tr></thead><tbody>{recs.data.map((r) => (
        <tr key={r.record_ref}><td className="mono small">{r.record_ref}<p className="mute">{r.hash.slice(0, 18)}…</p></td><td>{r.version}</td><td><span className={`pill ${r.status === 'anchored' ? 'ok' : r.status === 'pending' ? 'warn' : r.status === 'failed' ? 'bad' : ''}`}>{r.status}</span>{r.error && <p className="small err">{r.error}</p>}</td><td>{r.block_number ?? '—'}</td><td className="small">{fmt(r.created_at)}</td>
          <td>{res[r.record_ref] ? <><span className={`pill ${TONE[res[r.record_ref].result]}`}>{res[r.record_ref].result}</span><p className="small mute">{res[r.record_ref].message}</p></> : r.last_verified}</td>
          <td><button className="btn ghost sm" onClick={() => verify(r.record_ref)}>Verify</button></td></tr>))}</tbody></table></div> : <Empty title="No records yet">A record is created whenever an assessment is graded.</Empty>}
    </Page>
  )
}
