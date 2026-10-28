import { useState } from 'react'
import { api, errText } from '../../lib/api'
import { useGet } from '../../lib/hooks'
import { Empty, ErrorBox, Label, Loading, Page } from '../../ui/bits.jsx'

export default function Rewards() {
  const badges = useGet('/student/badges')
  const lb = useGet('/student/leaderboard')
  const priv = useGet('/student/privacy')
  const xp = useGet('/student/xp-history')
  const [alias, setAlias] = useState('')
  const [msg, setMsg] = useState('')
  const [shareUrl, setShareUrl] = useState('')
  const save = async (patch) => {
    setMsg('')
    try { await api.patch('/student/privacy', patch); priv.reload(); lb.reload(); setMsg('Saved.') } catch (e) { setMsg(errText(e)) }
  }
  const share = async (b) => {
    if (!confirm(`Create a public link for “${b.name}”? Anyone with the link can see the badge (not your name unless you choose).`)) return
    const inc = confirm('Include your name on the shared page? OK = yes, Cancel = stay anonymous.')
    try { const { data } = await api.post(`/student/badges/${b.earned_id}/share`, { confirm: true, include_name: inc }); setShareUrl(`${location.origin}${data.path}`) } catch (e) { setMsg(errText(e)) }
  }
  if (badges.loading) return <Loading />
  return (
    <Page label="Rewards" title="Rewards">
      <Label n={1}>Badges</Label>
      <div className="grid g3" style={{ marginTop: 16 }}>{badges.data?.map((b) => (
        <div key={b.id} className="card" style={{ opacity: b.earned ? 1 : .55 }}>
          <div className="row between"><span className="h3">{b.name}</span><span className={`pill ${b.earned ? 'acc' : ''}`}>{b.earned ? 'earned' : 'locked'}</span></div>
          <p className="mute small" style={{ margin: '8px 0' }}>{b.description}</p><p className="label">Criteria: {b.criteria}</p>
          {b.earned && <button className="btn ghost sm" style={{ marginTop: 12 }} onClick={() => share(b)}>Share…</button>}</div>))}</div>
      {shareUrl && <p className="okmsg" style={{ marginTop: 12 }}>Share link: <a href={shareUrl} style={{ textDecoration: 'underline' }}>{shareUrl}</a></p>}

      <div className="divider" /><Label n={2}>Leaderboard (opt-in)</Label>
      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="card"><p className="mute small">You appear only if you opt in, under an alias — never your name or ID.</p>
          <div className="field" style={{ margin: '14px 0' }}><label htmlFor="al">Display alias</label><input id="al" className="input" defaultValue={priv.data?.display_alias || ''} onChange={(e) => setAlias(e.target.value)} maxLength={20} /></div>
          <div className="row"><button className="btn sm" onClick={() => save({ leaderboard_opt_in: true, display_alias: alias || priv.data?.display_alias || undefined })}>{priv.data?.leaderboard_opt_in ? 'Update alias' : 'Join leaderboard'}</button>
            {priv.data?.leaderboard_opt_in && <button className="btn ghost sm" onClick={() => save({ leaderboard_opt_in: false })}>Leave</button>}</div><ErrorBox msg={msg && !msg.startsWith('Saved') ? msg : ''} /></div>
        <div className="card">{lb.data?.entries.length ? <table><tbody>{lb.data.entries.map((e) => <tr key={e.rank} style={e.you ? { color: 'var(--accent)' } : null}><td>#{e.rank}</td><td>{e.alias}</td><td>L{e.level}</td><td>{e.xp} XP</td></tr>)}</tbody></table> : <Empty title="No one yet">Nobody in your class has opted in.</Empty>}</div></div>

      <div className="divider" /><Label n={3}>AI chat privacy</Label>
      <div className="card" style={{ marginTop: 16 }}><div className="field"><label htmlFor="ret">Keep my AI conversations for</label>
        <select id="ret" className="input" style={{ maxWidth: 260 }} value={priv.data?.conversation_retention_days ?? 90} onChange={(e) => save({ conversation_retention_days: +e.target.value })}>
          <option value={0}>Do not store</option><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option><option value={365}>1 year</option></select></div>
        {msg.startsWith('Saved') && <p className="okmsg">Saved.</p>}</div>

      <div className="divider" /><Label n={4}>XP history</Label>
      <div className="table-wrap"><table><tbody>{xp.data?.map((x, i) => <tr key={i}><td>+{x.amount}</td><td>{x.description}</td><td className="mute">{new Date(x.at).toLocaleDateString()}</td></tr>)}</tbody></table></div>
    </Page>
  )
}
