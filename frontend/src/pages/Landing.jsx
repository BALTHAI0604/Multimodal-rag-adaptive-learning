import { Link } from 'react-router-dom'
import { Accordion, Label, Lines, Marquee, Reveal } from '../ui/bits.jsx'
import { useReveal } from '../lib/hooks'
import { useAuth } from '../lib/auth.jsx'

const CAPS = [
  ['Grounded answers', 'Ask in Tamil, English, Kannada, Malayalam or Hindi. Every answer cites your faculty’s own material, or says plainly that it has none.'],
  ['Assessments from material', 'Twelve question types, Bloom levels, faculty preview and approval — generated from what was actually taught.'],
  ['Spoken answers', 'Prep timer, speaking timer, transcript you can check. Graded on concepts, never on accent.'],
  ['Adaptive path', 'Mastery, misconceptions and a revision queue built from your real attempts. Nothing invented.'],
  ['Honest integrity', 'Browser signals are logged for a human to review. No automatic punishment, no pretending to close tabs.'],
  ['Tamper-evident records', 'Only a keyed hash of each result is anchored on a local chain. Marks and names never leave the database.'],
]
const STEPS = [
  ['Faculty upload', 'PDF, DOCX, PPTX, text, scans, lecture audio. Text is extracted, OCR’d if needed, and indexed per class and subject.'],
  ['Questions are drafted', 'The model proposes; faculty preview, edit, regenerate and approve before anything reaches a student.'],
  ['Students learn and answer', 'Typed or spoken. Scoring happens on the server; answer keys stay hidden until release.'],
  ['The platform adapts', 'Mastery updates, misconceptions are tracked, and the next practice set fits the student.'],
  ['Results are anchored', 'A hash is written on-chain. Anyone permitted can verify the record still matches.'],
]
const FAQ = [
  { q: 'Does the AI see other classes’ material?', a: 'No. Retrieval is filtered by your enrolled subjects, class and faculty approval before ranking, so restricted text never reaches the model.' },
  { q: 'Is anything stored on the blockchain about me?', a: 'Only an opaque record id and a keyed hash. No marks, names, e-mails or transcripts.' },
  { q: 'Can the exam monitor see my face?', a: 'No automatic face judgement is made. The browser reports tab, window and fullscreen changes; faculty review them.' },
  { q: 'What if the AI model is offline?', a: 'The platform says so, falls back to extracting relevant passages, and generates questions from templates. It never fakes a model answer.' },
]
const ROLES = [['Students', 'Learn, practise, speak, earn'], ['Faculty', 'Teach, generate, review'], ['Advisors', 'Notice, support, follow up'], ['Management', 'Aggregate insight only']]

export default function Landing() {
  const ref = useReveal()
  const { user } = useAuth()
  return (
    <div ref={ref}>
      <nav className="nav" aria-label="Primary">
        <span className="brand"><span className="dot" />Adaptive</span>
        <a href="#about">About</a><a href="#capabilities">Capabilities</a><a href="#process">Process</a><a href="#faq">FAQ</a>
        <Link to={user ? '/app' : '/login'} className="active">{user ? 'Open app' : 'Sign in'}</Link>
      </nav>

      <header className="hero wrap">
        <div className="orb" />
        <div className="label" style={{ marginBottom: 28 }}><b>(00)</b> — Multimodal RAG · Adaptive learning · Blockchain-verified records</div>
        <Lines lines={['Learning that', 'listens, adapts', 'and proves it.']} />
        <div className="meta">
          <p className="lead reveal in">One platform for students, faculty, advisors and management — grounded in your own course material, free to run on your own machine.</p>
          <div className="row"><Link to="/login" className="btn accent" data-cursor="Enter">Sign in <span className="arrow">→</span></Link><a href="#about" className="btn ghost">Scroll ↓</a></div>
        </div>
      </header>

      <Marquee items={['Multilingual', 'Grounded', 'Adaptive', 'Voice-ready', 'Private by design', 'Verifiable']} />

      <section className="section wrap" id="about">
        <div className="sec-head"><Label n={1}>About</Label></div>
        <Reveal><p className="h1" style={{ maxWidth: '22ch' }}>Not another quiz app. A <span className="serif">teaching assistant</span> that only says what your faculty taught.</p></Reveal>
        <div className="grid g4" style={{ marginTop: 56 }}>
          {ROLES.map(([t, s], i) => <Reveal key={t} d={i * 0.08} className="card hover" data-cursor="Role"><div className="label">0{i + 1}</div><div className="h2" style={{ marginTop: 28 }}>{t}</div><p className="mute">{s}</p></Reveal>)}
        </div>
      </section>

      <section className="section wrap" id="capabilities">
        <div className="sec-head"><Label n={2}>Capabilities</Label><p className="mute small">Hover a row</p></div>
        {CAPS.map(([t, d], i) => (
          <Reveal key={t} className="index-row" data-cursor="Open"><span className="label">0{i + 1}</span><h3>{t}</h3><p>{d}</p></Reveal>
        ))}
      </section>

      <section className="section wrap" id="process">
        <div className="sec-head"><Label n={3}>Process</Label><h2 className="h2" style={{ maxWidth: '16ch' }}>From a faculty upload to a verified result.</h2></div>
        <div className="timeline">
          {STEPS.map(([t, d], i) => <div key={t} className="tl-item"><div className="label">Step 0{i + 1}</div><h3 className="h2" style={{ margin: '6px 0' }}>{t}</h3><p className="mute" style={{ maxWidth: '60ch' }}>{d}</p></div>)}
        </div>
      </section>

      <section className="section wrap" id="faq">
        <div className="sec-head"><Label n={4}>FAQ</Label><h2 className="h2">Straight answers.</h2></div>
        <Accordion items={FAQ} />
      </section>

      <footer className="footer wrap">
        <Reveal><p className="display" style={{ fontSize: 'clamp(48px,12vw,200px)' }}>Ready<span className="serif">?</span></p></Reveal>
        <div className="row between" style={{ marginTop: 36 }}>
          <Link to="/login" className="btn accent" data-cursor="Go">Sign in <span className="arrow">→</span></Link>
          <span className="label">Accounts are created by your administrator.</span>
        </div>
      </footer>
    </div>
  )
}
