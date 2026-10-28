"""Student: dashboard, assessments (start/answer/voice/violations/submit/review), privacy, gamification, practice."""
from __future__ import annotations

import datetime
import random
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.adaptive.mastery_engine import adaptive_mastery_engine
from app.ai import question_generator as qg
from app.assessment import service as svc
from app.assessment.grading import opaque, uses_options
from app.auth import permissions as perm
from app.auth.dependencies import require_roles
from app.config import settings
from app.database import get_db
from app.gamification import engine as gami
from app.models.models import (
    Assessment, AssessmentAttempt, AssessmentViolation, Badge, BadgeShare, LearningEvent, LearningMaterial, Question,
    QuestionOption, Recommendation, RevisionQueue, Student, StudentAnswer, StudentBadge, Subject, Topic, TopicMastery,
    Unit, User, VoiceAnswer, XPTransaction, utcnow,
)
from app.rag import pipeline
from app.schemas.schemas import (
    AnswerIn, PracticeRequest, PrivacyUpdate, ShareRequest, StartAttemptRequest, SubmitRequest, ViolationIn,
)
from app.security.audit import audit
from app.security.ratelimit import limiter
from app.speech import stt

router = APIRouter(prefix="/student", tags=["Student"])
StudentOnly = Depends(require_roles("student"))
GRACE = datetime.timedelta(seconds=settings.ASSESSMENT_GRACE_SECONDS)


def _st(db, user) -> Student:
    return perm.student_of(db, user)


def _iso(d):
    return d.isoformat() + "Z" if d else None


def _expire_stale(db: Session, student: Student) -> None:
    """Auto-submit any attempt whose deadline has passed (server-enforced time limit)."""
    now = utcnow()
    for at in db.query(AssessmentAttempt).filter(AssessmentAttempt.student_id == student.id,
                                                 AssessmentAttempt.status == "in_progress",
                                                 AssessmentAttempt.deadline_at < now):
        svc.grade_attempt(db, at, status="auto_submitted")


def _window(a: Assessment, now=None) -> str:
    now = now or utcnow()
    if a.status == "closed":
        return "closed"
    if a.start_time and now < a.start_time:
        return "upcoming"
    if a.end_time and now > a.end_time:
        return "closed"
    return "open"


def _own_attempt(db: Session, student: Student, attempt_id: int) -> AssessmentAttempt:
    at = db.query(AssessmentAttempt).filter_by(id=attempt_id).first()
    if not at or at.student_id != student.id:
        raise perm.not_found("Attempt")        # 404 (not 403): do not reveal other students' attempt ids
    return at


def _active(at: AssessmentAttempt) -> None:
    if at.status != "in_progress":
        raise HTTPException(409, "This attempt has already been submitted.")


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard")
def dashboard(user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    _expire_stale(db, st)
    subj_ids = perm.student_subject_ids(db, st)
    today = None
    tq = db.query(Topic).join(Unit).filter(Unit.subject_id.in_(subj_ids or {-1}), Topic.is_today_topic.is_(True)).all()
    tq = [t for t in tq if t.today_class_id in (None, st.class_id)]
    if tq:
        t = tq[0]
        mats = [m for m in db.query(LearningMaterial).filter_by(topic_id=t.id) if perm.material_visible_to_student(db, m, st)]
        today = {"topic_id": t.id, "title": t.title, "unit": t.unit.title, "unit_number": t.unit.unit_number,
                 "subject": {"id": t.unit.subject.id, "code": t.unit.subject.code, "name": t.unit.subject.name},
                 "materials_count": len(mats)}
    prof = adaptive_mastery_engine.profile(db, st.id)
    cur, nxt = gami.level_for(st.total_xp or 0)
    recs = db.query(Recommendation).filter_by(student_id=st.id, status="pending").order_by(Recommendation.id).limit(8).all()
    queue = db.query(RevisionQueue).filter_by(student_id=st.id, completed=False).order_by(RevisionQueue.priority_order).limit(8).all()
    pending = [a for a in _visible_assessments(db, st) if a["attempt_status"] in (None, "in_progress") and a["window"] == "open"]
    return {"student": {"name": user.full_name, "roll_number": st.roll_number, "department": st.department,
                        "semester": st.semester, "class_name": st.class_group.name if st.class_group else st.class_name},
            "today_topic": today, "mastery": prof, "unit_progress": adaptive_mastery_engine.unit_progress(db, st.id),
            "pending_assessments": pending,
            "gamification": {"xp": st.total_xp or 0, "level": cur[0], "level_title": cur[1],
                             "next_level_xp": nxt[2] if nxt else None, "streak": gami.effective_streak(st),
                             "longest_streak": st.longest_streak or 0},
            "recommendations": [{"id": r.id, "title": r.title, "description": r.description, "reason": r.reason,
                                 "priority": r.priority, "minutes": r.estimated_minutes, "topic_id": r.topic_id} for r in recs],
            "revision_queue": [{"topic_id": q.topic_id, "reason": q.reason, "minutes": q.estimated_time_minutes,
                                "topic": db.query(Topic.title).filter_by(id=q.topic_id).scalar()} for q in queue],
            "empty": not prof["topics"]}


# ------------------------------------------------------------------ assessments
def _visible_assessments(db: Session, st: Student):
    out = []
    q = db.query(Assessment).filter(Assessment.kind == "daily", Assessment.status.in_(["published", "closed"]),
                                    Assessment.subject_id.in_(perm.student_subject_ids(db, st) or {-1}))
    for a in q.order_by(Assessment.id.desc()):
        if not perm.assessment_visible_to_student(db, a, st):
            continue
        atts = db.query(AssessmentAttempt).filter_by(assessment_id=a.id, student_id=st.id).order_by(AssessmentAttempt.id).all()
        last = atts[-1] if atts else None
        out.append({"id": a.id, "title": a.title, "subject": a.subject.name, "subject_code": a.subject.code,
                    "topic": a.topic.title, "questions": len(a.questions), "total_marks": a.total_marks,
                    "time_limit_minutes": a.time_limit_minutes, "start_time": _iso(a.start_time), "end_time": _iso(a.end_time),
                    "window": _window(a), "max_attempts": a.max_attempts, "attempts_used": len(atts),
                    "attempt_id": last.id if last else None, "attempt_status": last.status if last else None,
                    "require_fullscreen": a.require_fullscreen and not st.exempt_fullscreen,
                    "require_preflight": a.require_preflight, "has_voice": any(q.question_type == "voice" for q in a.questions),
                    "integrity_policy": a.integrity_policy})
    return out


@router.get("/assessments")
def list_assessments(user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    _expire_stale(db, st)
    return _visible_assessments(db, st)


@router.post("/assessments/{assessment_id}/start")
def start(assessment_id: int, body: StartAttemptRequest, request: Request, user: User = StudentOnly,
          db: Session = Depends(get_db)):
    limiter.check(f"start:{user.id}", 20, 300)
    st = _st(db, user)
    _expire_stale(db, st)
    a = db.query(Assessment).filter_by(id=assessment_id).first()
    if not a or not perm.assessment_visible_to_student(db, a, st):
        raise perm.not_found("Assessment")
    w = _window(a)
    if w == "upcoming":
        raise HTTPException(409, "This assessment has not opened yet.")
    if w == "closed" and a.kind != "practice":
        raise HTTPException(409, "This assessment is closed.")
    existing = db.query(AssessmentAttempt).filter_by(assessment_id=a.id, student_id=st.id).all()
    live = next((x for x in existing if x.status == "in_progress"), None)
    if live:
        attempt = live                                       # resume
    else:
        if len(existing) >= (a.max_attempts or 1):
            raise HTTPException(409, "You have used all attempts for this assessment.")
        if a.require_preflight and a.kind != "practice":
            pf = body.preflight
            has_voice = any(q.question_type == "voice" for q in a.questions)
            if not pf or (has_voice and not st.typed_voice_alternative and not pf.mic_ok):
                raise HTTPException(422, "Complete the pre-assessment check (microphone) first, or ask your advisor "
                                         "for a typed-answer accommodation.")
        now = utcnow()
        attempt = AssessmentAttempt(assessment_id=a.id, student_id=st.id, attempt_no=len(existing) + 1,
                                    seed=random.randint(1, 2**31 - 1), started_at=now,
                                    deadline_at=svc.deadline_for(a, st, now), status="in_progress",
                                    preflight_json=body.preflight.model_dump() if body.preflight else None)
        db.add(attempt)
        db.commit()
        db.add(LearningEvent(student_id=st.id, event_type="assessment_started", subject_id=a.subject_id,
                             topic_id=a.topic_id, metadata_json={"assessment_id": a.id}))
        db.commit()
    qs = [svc.student_view(q, attempt, a.shuffle_options) for q in svc.ordered_questions(a, attempt)]
    saved = {r.question_id: {"text": r.answer_text if not uses_options(r.question) else None,
                             "selected_option_id": r.answer_text if uses_options(r.question) and r.selected_option_id is None else None,
                             "json": r.answer_json} for r in attempt.answers}
    return {"attempt_id": attempt.id, "assessment": {"id": a.id, "title": a.title, "kind": a.kind,
            "time_limit_minutes": a.time_limit_minutes, "integrity_policy": a.integrity_policy,
            "max_violations": a.max_violations, "require_fullscreen": a.require_fullscreen and not st.exempt_fullscreen,
            "typed_voice_alternative": bool(st.typed_voice_alternative)},
            "deadline_at": _iso(attempt.deadline_at), "server_time": _iso(utcnow()), "questions": qs, "saved": saved,
            "violation_count": attempt.violation_count}


@router.put("/attempts/{attempt_id}/answer")
def save_answer(attempt_id: int, body: AnswerIn, user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    at = _own_attempt(db, st, attempt_id)
    _active(at)
    if utcnow() > at.deadline_at + GRACE:
        svc.grade_attempt(db, at, status="auto_submitted")
        raise HTTPException(409, "Time is up; your saved answers were submitted.")
    q = db.query(Question).filter_by(id=body.question_id, assessment_id=at.assessment_id).first()
    if not q:
        raise HTTPException(422, "That question is not part of this assessment.")
    if body.selected_option_id is not None and uses_options(q):
        valid = {opaque(at.id, q.id, "opt", o.id) for o in q.options}
        if body.selected_option_id not in valid:
            raise HTTPException(422, "Invalid option.")
    if body.selected_option_id is not None and not uses_options(q):
        raise HTTPException(422, "This question does not take an option answer.")
    svc.save_answer(db, at, q, body.selected_option_id, body.answer_text, body.answer_json, body.input_mode)
    db.commit()
    return {"saved": True, "server_time": _iso(utcnow())}


@router.post("/attempts/{attempt_id}/voice/{question_id}")
def voice_answer(attempt_id: int, question_id: int, request: Request, language: str = Form("English"),
                 duration_seconds: float = Form(0), audio: UploadFile = File(...),
                 user: User = StudentOnly, db: Session = Depends(get_db)):
    limiter.check(f"voice:{user.id}", 40, 600)
    st = _st(db, user)
    at = _own_attempt(db, st, attempt_id)
    _active(at)
    if utcnow() > at.deadline_at + GRACE:
        raise HTTPException(409, "Time is up.")
    q = db.query(Question).filter_by(id=question_id, assessment_id=at.assessment_id, question_type="voice").first()
    if not q:
        raise HTTPException(422, "That is not a voice question of this assessment.")
    if duration_seconds > (q.voice_max_time_sec or 120) + 5:
        raise HTTPException(422, "The recording is longer than the allowed speaking time.")
    ext = Path(audio.filename or "a.webm").suffix.lower()
    if ext not in {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4"}:
        raise HTTPException(415, "Unsupported audio type.")
    folder = settings.UPLOAD_DIR / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}{ext}"
    size, limit = 0, settings.MAX_AUDIO_ANSWER_MB * 1024 * 1024
    with open(path, "wb") as out:
        while chunk := audio.file.read(1024 * 256):
            size += len(chunk)
            if size > limit:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, "Audio file too large.")
            out.write(chunk)
    try:
        tr = stt.transcribe_file(str(path), language)
    except stt.SpeechUnavailable as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(503, f"{exc} You can type your answer instead.")
    except Exception:
        path.unlink(missing_ok=True)                 # never leave a recording behind on an unexpected failure
        raise
    keep = settings.VOICE_STORE_AUDIO
    if not keep:
        path.unlink(missing_ok=True)
    if not tr.text.strip():
        raise HTTPException(422, "No speech was detected. Please try again or type your answer.")
    row = svc.save_answer(db, at, q, None, tr.text, None, "voice")
    db.flush()
    va = db.query(VoiceAnswer).filter_by(student_answer_id=row.id).first()
    if va is None:
        va = VoiceAnswer(student_answer_id=row.id, transcript=tr.text)
        db.add(va)
    va.transcript, va.duration_seconds, va.language = tr.text, tr.duration or duration_seconds, tr.language or language
    va.stt_engine = tr.engine
    va.audio_file_path = str(path) if keep else None
    va.audio_deleted_at = None if keep else utcnow()
    db.commit()
    return {"transcript": tr.text, "language": va.language, "engine": tr.engine,
            "note": "Review the transcript; you may edit it before submitting. Grading is on content, not accent."}


@router.post("/attempts/{attempt_id}/violations")
def violation(attempt_id: int, body: ViolationIn, user: User = StudentOnly, db: Session = Depends(get_db)):
    """Browser-signal log only. Nothing here can close tabs; nothing here punishes automatically."""
    limiter.check(f"viol:{user.id}", 120, 300)
    st = _st(db, user)
    at = _own_attempt(db, st, attempt_id)
    _active(at)
    a = at.assessment
    sev = {"tab_hidden": "medium", "window_blur": "low", "fullscreen_exit": "medium", "copy_paste": "low",
           "camera_lost": "low", "mic_lost": "low"}[body.violation_type]
    db.add(AssessmentViolation(attempt_id=at.id, student_id=st.id, violation_type=body.violation_type,
                               description=body.detail, client_timestamp=body.client_time, severity=sev))
    at.violation_count = (at.violation_count or 0) + 1
    at.integrity_review_required = True
    db.commit()
    over = at.violation_count >= (a.max_violations or 3)
    action = "log"
    if a.integrity_policy == "warn":
        action = "warn"
    elif a.integrity_policy in ("pause", "submit") and over:
        action = a.integrity_policy
    if action == "submit":      # only when faculty explicitly chose this policy; still flagged for human review
        svc.grade_attempt(db, at, status="auto_submitted")
    return {"count": at.violation_count, "limit": a.max_violations, "action": action,
            "message": "This event was recorded for your faculty to review. No automatic penalty is applied."}


@router.post("/attempts/{attempt_id}/submit")
def submit(attempt_id: int, body: SubmitRequest, request: Request, user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    at = _own_attempt(db, st, attempt_id)
    if at.status != "in_progress":
        return _result(db, st, at)             # idempotent re-submit
    late = utcnow() > at.deadline_at + GRACE
    if not late:
        qmap = {q.id: q for q in at.assessment.questions}
        for ans in body.answers:
            q = qmap.get(ans.question_id)
            if not q:
                continue
            if ans.selected_option_id is not None and uses_options(q):
                if ans.selected_option_id not in {opaque(at.id, q.id, "opt", o.id) for o in q.options}:
                    continue
            elif ans.selected_option_id is not None:
                continue
            svc.save_answer(db, at, q, ans.selected_option_id, ans.answer_text, ans.answer_json, ans.input_mode)
        db.commit()
    svc.grade_attempt(db, at, status="auto_submitted" if late else "completed")
    audit(db, "ATTEMPT_SUBMITTED", "attempt", at.id, user=user, request=request, details={"late": late})
    return _result(db, st, at)


def _result(db: Session, st: Student, at: AssessmentAttempt) -> dict:
    a = at.assessment
    released = svc.answers_released(a)
    show_score = a.show_score_immediately or released
    d = {"attempt_id": at.id, "assessment": a.title, "status": at.status, "answers_released": released,
         "answers_release_at": _iso(svc.release_time(a)), "needs_review": at.needs_review,
         "grade_version": at.grade_version}
    if show_score:
        d.update({"score": at.total_score, "max_score": at.max_score, "percentage": at.percentage})
    else:
        d["message"] = "Your score will be shown after the assessment window closes."
    rec = at.__dict__.get("_rec")
    from app.models.models import BlockchainRecord
    br = db.query(BlockchainRecord).filter_by(attempt_id=at.id).order_by(BlockchainRecord.version.desc()).first()
    d["ledger"] = {"record_ref": br.record_ref, "status": br.status} if br else None
    if show_score and released:
        d["questions"] = []
        rows = {r.question_id: r for r in at.answers}
        for q in a.questions:
            r = rows.get(q.id)
            item = {"id": q.id, "question_text": q.question_text, "type": q.question_type, "marks": q.marks,
                    "score": (r.faculty_score_override if r and r.faculty_score_override is not None else (r.score_awarded if r else 0)),
                    "your_answer": r.answer_text if r else None, "feedback": r.ai_feedback if r else None,
                    "explanation": q.explanation, "faculty_comment": r.faculty_comment if r else None}
            if uses_options(q):
                item["correct_options"] = [o.option_text for o in q.options if o.is_correct]
            elif q.question_type in ("short_answer", "voice"):
                item["model_answer"] = q.answer_key_text
            elif q.question_type == "fill_blank":
                item["accepted_answers"] = q.accepted_answers_json
            elif q.question_type == "numerical":
                item["correct_value"] = q.numeric_answer
            elif q.question_type == "match_following":
                item["correct_pairs"] = q.match_pairs_json
            elif q.question_type == "sequence":
                item["correct_order"] = [x["text"] for x in (q.sequence_items_json or [])]
            d["questions"].append(item)
    return d


@router.get("/attempts/{attempt_id}/result")
def result(attempt_id: int, user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    at = _own_attempt(db, st, attempt_id)
    if at.status == "in_progress":
        raise HTTPException(409, "Attempt still in progress.")
    return _result(db, st, at)


@router.get("/attempts")
def history(user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    rows = db.query(AssessmentAttempt).filter_by(student_id=st.id).order_by(AssessmentAttempt.id.desc()).limit(100).all()
    return [{"attempt_id": r.id, "assessment": r.assessment.title, "kind": r.assessment.kind, "status": r.status,
             "percentage": r.percentage if r.assessment.show_score_immediately or svc.answers_released(r.assessment) else None,
             "completed_at": _iso(r.completed_at)} for r in rows]


# ------------------------------------------------------------------ practice (adaptive, private)
@router.post("/practice", status_code=201)
def practice(body: PracticeRequest, user: User = StudentOnly, db: Session = Depends(get_db)):
    limiter.check(f"practice:{user.id}", 10, 600)
    st = _st(db, user)
    topic = db.query(Topic).filter_by(id=body.topic_id).first()
    if not topic:
        raise perm.not_found("Topic")
    subject = perm.require_student_subject(db, st, topic.unit.subject_id)
    chunks = [c for c in pipeline.retrieve_for_topic(db, topic.id)
              if perm.material_visible_to_student(db, db.query(LearningMaterial).get(c["material_id"]), st)]
    if not chunks:
        raise HTTPException(409, "There is no approved material for this topic yet.")
    m = db.query(TopicMastery).filter_by(student_id=st.id, topic_id=topic.id).first()
    diff = m.current_difficulty if m else "easy"
    types = ["mcq", "true_false", "fill_blank", "short_answer"]
    res = qg.generate_questions(topic.title, subject.name, chunks, "\n".join(c["content"] for c in chunks), body.count,
                                types, "mixed", diff, None, seed=random.randint(1, 10**6))
    if not res["questions"]:
        raise HTTPException(422, "Could not build practice questions from the available material.")
    fac_id = subject.faculty_id
    if not fac_id:
        from app.models.models import SubjectAssignment
        sa = db.query(SubjectAssignment).filter_by(subject_id=subject.id).first()
        fac_id = sa.faculty_id if sa else None
    if not fac_id:
        raise HTTPException(409, "This subject has no assigned faculty yet.")
    a = Assessment(subject_id=subject.id, unit_id=topic.unit_id, topic_id=topic.id, faculty_id=fac_id, kind="practice",
                   owner_student_id=st.id, title=f"Practice: {topic.title}", status="published", is_published=True,
                   time_limit_minutes=max(5, body.count * 2), difficulty_target=diff, max_attempts=1,
                   shuffle_questions=True, shuffle_options=True, generation_info=res["info"], published_at=utcnow())
    db.add(a)
    db.flush()
    for q in res["questions"]:
        row = Question(assessment_id=a.id, order_index=q["order_index"], question_text=q["question_text"],
                       question_type=q["question_type"], bloom_level=q["bloom_level"], marks=q["marks"],
                       difficulty=q["difficulty"], explanation=q.get("explanation"),
                       accepted_answers_json=q.get("accepted_answers_json"), answer_key_text=q.get("answer_key_text"),
                       rubric_json=q.get("rubric_json"), generation_method=q.get("generation_method"),
                       content_hash=q.get("content_hash"))
        for o in q.get("options", []):
            row.options.append(QuestionOption(option_text=o["option_text"], is_correct=bool(o.get("is_correct")),
                                              explanation=o.get("explanation"), misconception=o.get("misconception")))
        db.add(row)
    db.flush()
    db.refresh(a)
    a.total_marks = sum(q.marks for q in a.questions)
    db.commit()
    return {"assessment_id": a.id, "questions": len(a.questions), "difficulty": diff}


# ------------------------------------------------------------------ materials
@router.get("/materials")
def materials(subject_id: Optional[int] = None, topic_id: Optional[int] = None, user: User = StudentOnly,
              db: Session = Depends(get_db)):
    st = _st(db, user)
    q = db.query(LearningMaterial).filter(LearningMaterial.subject_id.in_(perm.student_subject_ids(db, st) or {-1}))
    if subject_id:
        q = q.filter_by(subject_id=subject_id)
    if topic_id:
        q = q.filter_by(topic_id=topic_id)
    return [{"id": m.id, "title": m.title, "type": m.material_type, "subject_id": m.subject_id, "topic_id": m.topic_id,
             "file_name": m.file_name} for m in q.order_by(LearningMaterial.id.desc()) if perm.material_visible_to_student(db, m, st)]


@router.get("/materials/{material_id}/download")
def download(material_id: int, user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    m = db.query(LearningMaterial).filter_by(id=material_id).first()
    if not m or not perm.material_visible_to_student(db, m, st) or not m.file_path or not Path(m.file_path).exists():
        raise perm.not_found("Material")
    return FileResponse(m.file_path, filename=m.file_name)


@router.get("/subjects")
def my_subjects(user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    subs = db.query(Subject).filter(Subject.id.in_(perm.student_subject_ids(db, st) or {-1})).order_by(Subject.code)
    return [{"id": s.id, "code": s.code, "name": s.name, "units": [
        {"id": u.id, "unit_number": u.unit_number, "title": u.title,
         "topics": [{"id": t.id, "title": t.title} for t in u.topics]} for u in s.units]} for s in subs]


# ------------------------------------------------------------------ privacy, leaderboard, badges
@router.get("/privacy")
def get_privacy(user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    return {"leaderboard_opt_in": bool(st.leaderboard_opt_in), "display_alias": st.display_alias,
            "conversation_retention_days": st.conversation_retention_days}


@router.patch("/privacy")
def set_privacy(body: PrivacyUpdate, request: Request, user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    data = body.model_dump(exclude_unset=True)
    if data.get("leaderboard_opt_in") and not (data.get("display_alias") or st.display_alias):
        raise HTTPException(422, "Choose a display alias before joining the leaderboard.")
    for k, v in data.items():
        setattr(st, k, v)
    db.commit()
    audit(db, "PRIVACY_UPDATED", "student", st.id, user=user, request=request, details=list(data))
    return get_privacy(user, db)


@router.get("/leaderboard")
def leaderboard(user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    rows = db.query(Student).filter(Student.leaderboard_opt_in.is_(True), Student.class_id == st.class_id,
                                    Student.display_alias.isnot(None)).order_by(Student.total_xp.desc()).limit(20).all()
    return {"opted_in": bool(st.leaderboard_opt_in),
            "entries": [{"rank": i + 1, "alias": r.display_alias, "xp": r.total_xp, "level": r.current_level,
                         "you": r.id == st.id} for i, r in enumerate(rows)],
            "note": "Only students who opted in appear, under their chosen alias."}


@router.get("/badges")
def badges(user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    gami.ensure_badges(db)
    earned = {sb.badge_id: sb for sb in db.query(StudentBadge).filter_by(student_id=st.id)}
    out = []
    for b in db.query(Badge).order_by(Badge.id):
        sb = earned.get(b.id)
        out.append({"id": b.id, "code": b.code, "name": b.name, "description": b.description, "criteria": b.criteria,
                    "icon": b.icon, "earned": bool(sb), "earned_id": sb.id if sb else None,
                    "awarded_at": _iso(sb.awarded_at) if sb else None})
    return out


@router.get("/xp-history")
def xp_history(user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    rows = db.query(XPTransaction).filter_by(student_id=st.id).order_by(XPTransaction.id.desc()).limit(50)
    return [{"amount": r.amount, "description": r.description, "at": _iso(r.created_at)} for r in rows]


@router.post("/badges/{student_badge_id}/share")
def share_badge(student_badge_id: int, body: ShareRequest, request: Request, user: User = StudentOnly,
                db: Session = Depends(get_db)):
    st = _st(db, user)
    if not body.confirm:
        raise HTTPException(422, "Sharing must be confirmed explicitly.")
    sb = db.query(StudentBadge).filter_by(id=student_badge_id, student_id=st.id).first()
    if not sb:
        raise perm.not_found("Badge")
    sh = BadgeShare(student_badge_id=sb.id, token=gami.new_share_token(), include_name=body.include_name)
    db.add(sh)
    db.commit()
    audit(db, "BADGE_SHARED", "student_badge", sb.id, user=user, request=request)
    return {"token": sh.token, "path": f"/b/{sh.token}"}


@router.delete("/badge-shares/{token}")
def revoke_share(token: str, user: User = StudentOnly, db: Session = Depends(get_db)):
    st = _st(db, user)
    sh = db.query(BadgeShare).join(StudentBadge).filter(BadgeShare.token == token, StudentBadge.student_id == st.id).first()
    if not sh:
        raise perm.not_found("Share")
    sh.revoked_at = utcnow()
    db.commit()
    return {"status": "revoked"}
