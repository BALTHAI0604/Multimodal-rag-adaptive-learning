import { lazy, Suspense, useEffect } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from './lib/auth.jsx'
import { Cursor, ErrorBoundary, Loading } from './ui/bits.jsx'

const Landing = lazy(() => import('./pages/Landing.jsx'))
const Login = lazy(() => import('./pages/Login.jsx'))
const Badge = lazy(() => import('./pages/PublicBadge.jsx'))
const StudentHome = lazy(() => import('./pages/student/Home.jsx'))
const Assessments = lazy(() => import('./pages/student/Assessments.jsx'))
const Take = lazy(() => import('./pages/student/Take.jsx'))
const Result = lazy(() => import('./pages/student/Result.jsx'))
const Companion = lazy(() => import('./pages/student/Companion.jsx'))
const Rewards = lazy(() => import('./pages/student/Rewards.jsx'))
const Materials = lazy(() => import('./pages/student/Materials.jsx'))
const FacultyHome = lazy(() => import('./pages/faculty/Home.jsx'))
const Curriculum = lazy(() => import('./pages/faculty/Curriculum.jsx'))
const Studio = lazy(() => import('./pages/faculty/Studio.jsx'))
const Review = lazy(() => import('./pages/faculty/Review.jsx'))
const Advisor = lazy(() => import('./pages/advisor/Advisor.jsx'))
const Management = lazy(() => import('./pages/management/Management.jsx'))
const Admin = lazy(() => import('./pages/admin/Admin.jsx'))
const Ledger = lazy(() => import('./pages/Ledger.jsx'))

const NAV = {
  student: [['/app', 'Today'], ['/app/assessments', 'Assessments'], ['/app/companion', 'Companion'], ['/app/materials', 'Materials'], ['/app/rewards', 'Rewards'], ['/app/ledger', 'Integrity']],
  faculty: [['/app', 'Overview'], ['/app/curriculum', 'Curriculum'], ['/app/studio', 'Assessments'], ['/app/review', 'Review'], ['/app/ledger', 'Integrity']],
  advisor: [['/app', 'Students'], ['/app/ledger', 'Integrity']],
  management: [['/app', 'Insights']],
  admin: [['/app', 'Admin'], ['/app/ledger', 'Ledger']],
}

function Shell({ children }) {
  const { user, logout } = useAuth()
  const nav = useNavigate()
  return (
    <>
      <nav className="nav" aria-label="Primary">
        <NavLink to="/" className="brand" data-cursor="Home"><span className="dot" />Adaptive</NavLink>
        {NAV[user.role].map(([to, label]) => <NavLink key={to} to={to} end={to === '/app'}>{label}</NavLink>)}
        <button onClick={async () => { await logout(); nav('/') }}>Sign out</button>
      </nav>
      {children}
    </>
  )
}

function Guard({ roles, children }) {
  const { user, ready } = useAuth()
  const loc = useLocation()
  if (!ready) return <Loading />
  if (!user) return <Navigate to="/login" state={{ from: loc.pathname }} replace />
  if (roles && !roles.includes(user.role)) return <Navigate to="/app" replace />
  return <Shell>{children}</Shell>
}

function Home() {
  const { user } = useAuth()
  return { student: <StudentHome />, faculty: <FacultyHome />, advisor: <Advisor />, management: <Management />, admin: <Admin /> }[user.role]
}

export default function App() {
  const { pathname } = useLocation()
  useEffect(() => { window.scrollTo(0, 0) }, [pathname])
  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <Cursor />
      <ErrorBoundary key={pathname}><Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Login />} />
          <Route path="/b/:token" element={<Badge />} />
          <Route path="/app" element={<Guard><Home /></Guard>} />
          <Route path="/app/assessments" element={<Guard roles={['student']}><Assessments /></Guard>} />
          <Route path="/app/take/:id" element={<Guard roles={['student']}><Take /></Guard>} />
          <Route path="/app/result/:attemptId" element={<Guard roles={['student']}><Result /></Guard>} />
          <Route path="/app/companion" element={<Guard roles={['student']}><Companion /></Guard>} />
          <Route path="/app/materials" element={<Guard roles={['student']}><Materials /></Guard>} />
          <Route path="/app/rewards" element={<Guard roles={['student']}><Rewards /></Guard>} />
          <Route path="/app/curriculum" element={<Guard roles={['faculty']}><Curriculum /></Guard>} />
          <Route path="/app/studio" element={<Guard roles={['faculty']}><Studio /></Guard>} />
          <Route path="/app/review" element={<Guard roles={['faculty']}><Review /></Guard>} />
          <Route path="/app/ledger" element={<Guard roles={['student', 'faculty', 'advisor', 'admin']}><Ledger /></Guard>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense></ErrorBoundary>
    </>
  )
}
