"""Faculty: assessment authoring (generate -> edit -> approve -> publish), grading review, integrity review,
analytics and student reviews.  Every route re-checks subject assignment server-side."""
from __future__ import annotations

import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai import question_generator as qg
from app.assessment import service as svc
from app.assessment.types import QUESTION_TYPES
from app.auth import permissions as perm
from app.auth.dependencies import require_roles
from app.models.models import (
    Assessment, AssessmentAttempt, AssessmentViolation, Faculty, Misconception, Question, QuestionOption, Student,
    StudentAnswer, Subject, TeacherReview, Topic, TopicMastery, Unit, User, VoiceAnswer, utcnow,
)
from app.rag import pipeline
from app.database import get_db
from app.schemas.schemas import (
    AssessmentSettings, FollowUpUpdate, GenerateRequest, GradeOverride, PublishRequest, QuestionIn, TeacherReviewIn,
    ViolationReview,
)
from app.security.audit import audit
from app.security.ratelimit import limiter

router = APIRouter(prefix="/faculty", tags=["Faculty assessments"])
FacultyOnly = Depends(require_roles("faculty"))


def _own_assessment(db: Session, user: User, assessment_id: int) -> Assessment:
    a = db.query(Assessment).filter_by(id=assessment_id).first()
    if not a or a.kind == "practice":
        raise perm.not_found("Assessment")
    perm.require_subject_faculty(db, user, a.subject_id)
    return a


def _q_full(q: Question) -> dict:
    """Faculty view: includes keys, rubric and explanations."""
    return {"id": q.id, "order_index": q.order_index, "question_type": q.question_type, "question_text": q.question_text,
            "bloom_level": q.bloom_level, "difficulty": q.difficulty, "marks": q.marks, "explanation": q.explanation,
            "options": [{"id": o.id, "option_text": o.option_text, "is_correct": o.is_correct,
                         "explanation": o.explanation, "misconception": o.misconception} for o in q.options],
            "match_pairs_json": q.match_pairs_json, "sequence_items_json": q.sequence_items_json,
            "assertion_text": q.assertion_text, "reason_text": q.reason_text,
            "accepted_answers_json": q.accepted_answers_json, "numeric_answer": q.numeric_answer,
            "numeric_tolerance": q.numeric_tolerance, "answer_key_text": q.answer_key_text, "rubric_json": q.rubric_json,
            "voice_guidance": q.voice_guidance, "voice_prep_sec": q.voice_prep_sec, "voice_max_time_sec": q.voice_max_time_sec,
            "generation_method": q.generation_method, "source_refs": q.source_refs_json}


def _a_view(a: Assessment, with_questions: bool = False) -> dict:
    d = {"id": a.id, "title": a.title, "description": a.description, "subject_id": a.subject_id, "unit_id": a.unit_id,
         "topic_id": a.topic_id, "class_id": a.class_id, "status": a.status, "time_limit_minutes": a.time_limit_minutes,
         "total_marks": a.total_marks, "start_time": a.start_time.isoformat() if a.start_time else None,
         "end_time": a.end_time.isoformat() if a.end_time else None,
         "answers_release_at": a.answers_release_at.isoformat() if a.answers_release_at else None,
         "negative_marking": a.negative_marking, "shuffle_questions": a.shuffle_questions,
         "shuffle_options": a.shuffle_options, "max_attempts": a.max_attempts, "require_fullscreen": a.require_fullscreen,
         "require_preflight": a.require_preflight, "integrity_policy": a.integrity_policy,
         "max_violations": a.max_violations, "show_score_immediately": a.show_score_immediately,
         "question_count": len(a.questions), "generation_info": a.generation_info,
         "created_at": a.created_at.isoformat() if a.created_at else None}
    if with_questions:
        d["questions"] = [_q_full(q) for q in a.questions]
    return d


def _apply_question(q: Question, body: QuestionIn) -> None:
    if body.question_type not in QUESTION_TYPES:
        raise HTTPException(422, f"Unknown question type. Allowed: {sorted(QUESTION_TYPES)}")
    raw = body.model_dump()
    raw["options"] = [o for o in raw["options"]]
    raw["question_text"] = body.question_text
    valid = qg.validate_question({**raw, "type": body.question_type}, body.question_type)
    if valid is None:
        raise HTTPException(422, "The question is incomplete or inconsistent for its type "
                                 "(e.g. an MCQ needs exactly one correct option; matching needs 3+ pairs).")
    for f in ("question_text", "question_type", "bloom_level", "difficulty", "marks", "explanation", "match_pairs_json",
              "sequence_items_json", "assertion_text", "reason_text", "accepted_answers_json", "numeric_answer",
              "numeric_tolerance", "answer_key_text", "rubric_json", "voice_guidance", "content_hash"):
        if f in valid:
            setattr(q, f, valid[f])
    q.voice_prep_sec = valid.get("voice_prep_sec", q.voice_prep_sec)
    q.voice_max_time_sec = valid.get("voice_max_time_sec", q.voice_max_time_sec)
    for o in list(q.options):
        q.options.remove(o)
    for o in valid.get("options", []):
        q.options.append(QuestionOption(option_text=o["option_text"], is_correct=bool(o.get("is_correct")),
                                        explanation=o.get("explanation"), misconception=o.get("misconception")))


def _retotal(a: Assessment) -> None:
    a.total_marks = round(sum(q.marks for q in a.questions), 2)
    a.pass_marks = round(a.total_marks * 0.4, 2)


# ------------------------------------------------------------------ generate / CRUD
@router.post("/assessments/generate", status_code=201)
def generate(body: GenerateRequest, request: Request, user: User = FacultyOnly, db: Session = Depends(get_db)):
    limiter.check(f"gen:{user.id}", 20, 600)
    subject = perm.require_subject_faculty(db, user, body.subject_id)
    fac = perm.faculty_of(db, user)
    unit = db.query(Unit).filter_by(id=body.unit_id, subject_id=subject.id).first()
    topic = db.query(Topic).filter_by(id=body.topic_id, unit_id=body.unit_id).first() if unit else None
    if not topic:
        raise HTTPException(422, "Unit/topic do not belong to this subject.")
    if body.class_id is not None:
        all_c, ids = perm.faculty_class_scope(db, fac, subject)
        if not all_c and body.class_id not in ids:
            raise perm.forbidden("You are not assigned to that class for this subject.")
    if body.types:
        bad = [t for t in body.types if t not in QUESTION_TYPES]
        if bad:
            raise HTTPException(422, f"Unknown question types: {bad}")
    chunks = pipeline.retrieve_for_topic(db, topic.id)
    if not chunks:
        raise HTTPException(409, "No approved, indexed material exists for this topic. Upload and index material first.")
    existing = {h for (h,) in db.query(Question.content_hash).join(Assessment).filter(
        Assessment.topic_id == topic.id, Question.content_hash.isnot(None))}
    try:
        res = qg.generate_questions(topic.title, subject.name, chunks, "\n".join(c["content"] for c in chunks),
                                    body.num_questions, body.types, body.bloom_focus, body.difficulty, existing,
                                    body.voice_prep_sec, body.voice_max_time_sec)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if not res["questions"]:
        raise HTTPException(422, "The available material was not sufficient to generate valid questions. "
                                 "Add richer material or start Ollama for better generation.")
    a = Assessment(subject_id=subject.id, unit_id=unit.id, topic_id=topic.id, faculty_id=fac.id, class_id=body.class_id,
                   title=body.title, kind="daily", status="draft", is_published=False,
                   time_limit_minutes=body.time_limit_minutes, bloom_level_focus=body.bloom_focus,
                   difficulty_target=body.difficulty, generation_info=res["info"],
                   source_material_ids=sorted({c["material_id"] for c in chunks}))
    db.add(a)
    db.flush()
    for q in res["questions"]:
        row = Question(assessment_id=a.id, order_index=q["order_index"], question_text=q["question_text"],
                       question_type=q["question_type"], bloom_level=q["bloom_level"], marks=q["marks"],
                       difficulty=q["difficulty"], explanation=q.get("explanation"),
                       match_pairs_json=q.get("match_pairs_json"), sequence_items_json=q.get("sequence_items_json"),
                       assertion_text=q.get("assertion_text"), reason_text=q.get("reason_text"),
                       answer_key_text=q.get("answer_key_text"), accepted_answers_json=q.get("accepted_answers_json"),
                       numeric_answer=q.get("numeric_answer"), numeric_tolerance=q.get("numeric_tolerance"),
                       rubric_json=q.get("rubric_json"), voice_guidance=q.get("voice_guidance"),
                       voice_prep_sec=q.get("voice_prep_sec", 10), voice_max_time_sec=q.get("voice_max_time_sec", 120),
                       source_refs_json=q.get("source_refs_json"), generation_method=q.get("generation_method"),
                       content_hash=q.get("content_hash"))
        for o in q.get("options", []):
            row.options.append(QuestionOption(option_text=o["option_text"], is_correct=bool(o.get("is_correct")),
                                              explanation=o.get("explanation"), misconception=o.get("misconception")))
        db.add(row)
    db.flush()
    db.refresh(a)
    _retotal(a)
    db.commit()
    audit(db, "ASSESSMENT_GENERATED", "assessment", a.id, user=user, request=request, details=res["info"])
    return _a_view(a, True)


@router.get("/assessments")
def list_assessments(subject_id: Optional[int] = None, status: Optional[str] = None, user: User = FacultyOnly,
                     db: Session = Depends(get_db)):
    f = perm.faculty_of(db, user)
    ids = perm.faculty_subject_ids(db, f)
    q = db.query(Assessment).filter(Assessment.subject_id.in_(ids or {-1}), Assessment.kind == "daily")
    if subject_id:
        if subject_id not in ids:
            raise perm.forbidden()
        q = q.filter_by(subject_id=subject_id)
    if status:
        q = q.filter_by(status=status)
    out = []
    for a in q.order_by(Assessment.id.desc()):
        v = _a_view(a)
        v["attempts"] = db.query(AssessmentAttempt).filter_by(assessment_id=a.id).count()
        out.append(v)
    return out


@router.get("/assessments/{assessment_id}")
def get_assessment(assessment_id: int, user: User = FacultyOnly, db: Session = Depends(get_db)):
    return _a_view(_own_assessment(db, user, assessment_id), True)


def _require_draft(a: Assessment) -> None:
    if a.status != "draft":
        raise HTTPException(409, "Only draft assessments can be edited. Close or duplicate it instead.")


@router.patch("/assessments/{assessment_id}")
def update_settings(assessment_id: int, body: AssessmentSettings, request: Request, user: User = FacultyOnly,
                    db: Session = Depends(get_db)):
    a = _own_assessment(db, user, assessment_id)
    data = body.model_dump(exclude_unset=True)
    if a.status != "draft":   # live assessments: only the schedule and release time may change
        data = {k: v for k, v in data.items() if k in {"end_time", "answers_release_at", "description"}}
    if "class_id" in data and data["class_id"] is not None:
        subject = db.query(Subject).filter_by(id=a.subject_id).first()
        all_c, ids = perm.faculty_class_scope(db, perm.faculty_of(db, user), subject)
        if not all_c and data["class_id"] not in ids:
            raise perm.forbidden("You are not assigned to that class.")
    for k, v in data.items():
        setattr(a, k, v)
    if a.start_time and a.end_time and a.end_time <= a.start_time:
        raise HTTPException(422, "end_time must be after start_time.")
    db.commit()
    audit(db, "ASSESSMENT_UPDATED", "assessment", a.id, user=user, request=request, details=list(data))
    return _a_view(a)


@router.post("/assessments/{assessment_id}/questions", status_code=201)
def add_question(assessment_id: int, body: QuestionIn, user: User = FacultyOnly, db: Session = Depends(get_db)):
    a = _own_assessment(db, user, assessment_id)
    _require_draft(a)
    q = Question(assessment_id=a.id, order_index=len(a.questions), question_text=body.question_text,
                 question_type=body.question_type, generation_method="manual")
    _apply_question(q, body)
    q.generation_method = "manual"
    db.add(q)
    db.flush()
    db.refresh(a)
    _retotal(a)
    db.commit()
    return _q_full(q)


@router.put("/assessments/{assessment_id}/questions/{question_id}")
def edit_question(assessment_id: int, question_id: int, body: QuestionIn, user: User = FacultyOnly,
                  db: Session = Depends(get_db)):
    a = _own_assessment(db, user, assessment_id)
    _require_draft(a)
    q = db.query(Question).filter_by(id=question_id, assessment_id=a.id).first()
    if not q:
        raise perm.not_found("Question")
    _apply_question(q, body)
    db.flush()
    db.refresh(a)
    _retotal(a)
    db.commit()
    return _q_full(q)


@router.delete("/assessments/{assessment_id}/questions/{question_id}")
def delete_question(assessment_id: int, question_id: int, user: User = FacultyOnly, db: Session = Depends(get_db)):
    a = _own_assessment(db, user, assessment_id)
    _require_draft(a)
    q = db.query(Question).filter_by(id=question_id, assessment_id=a.id).first()
    if not q:
        raise perm.not_found("Question")
    db.delete(q)
    db.flush()
    db.refresh(a)
    for i, x in enumerate(a.questions):
        x.order_index = i
    _retotal(a)
    db.commit()
    return {"status": "deleted"}


@router.post("/assessments/{assessment_id}/questions/{question_id}/regenerate")
def regenerate_question(assessment_id: int, question_id: int, user: User = FacultyOnly, db: Session = Depends(get_db)):
    """Replace one question with a freshly generated one of the same type (avoids duplicates)."""
    limiter.check(f"gen:{user.id}", 40, 600)
    a = _own_assessment(db, user, assessment_id)
    _require_draft(a)
    q = db.query(Question).filter_by(id=question_id, assessment_id=a.id).first()
    if not q:
        raise perm.not_found("Question")
    chunks = pipeline.retrieve_for_topic(db, a.topic_id)
    if not chunks:
        raise HTTPException(409, "No indexed material for this topic.")
    hashes = {h for (h,) in db.query(Question.content_hash).join(Assessment).filter(
        Assessment.topic_id == a.topic_id, Question.content_hash.isnot(None))}
    import random
    res = qg.generate_questions(a.topic.title, a.subject.name, chunks, "\n".join(c["content"] for c in chunks), 1,
                                [q.question_type], q.bloom_level, q.difficulty, hashes, seed=random.randint(1, 10**6))
    if not res["questions"]:
        raise HTTPException(422, "Could not generate a different valid question of that type from the material.")
    n = res["questions"][0]
    body = QuestionIn(**{k: n.get(k) for k in QuestionIn.model_fields if k in n and n.get(k) is not None})
    _apply_question(q, body)
    q.generation_method = n.get("generation_method")
    q.source_refs_json = n.get("source_refs_json")
    db.flush()
    db.refresh(a)
    _retotal(a)
    db.commit()
    return _q_full(q)


@router.post("/assessments/{assessment_id}/publish")
def publish(assessment_id: int, body: PublishRequest, request: Request, user: User = FacultyOnly,
            db: Session = Depends(get_db)):
    a = _own_assessment(db, user, assessment_id)
    _require_draft(a)
    if not a.questions:
        raise HTTPException(422, "Add at least one question before publishing.")
    if body.start_time:
        a.start_time = body.start_time
    if body.end_time:
        a.end_time = body.end_time
    if a.start_time and a.end_time and a.end_time <= a.start_time:
        raise HTTPException(422, "end_time must be after start_time.")
    key = f"{a.class_id or 'all'}:{a.topic_id}:{datetime.date.today().isoformat()}:{a.id}"
    a.activity_key = key
    a.status, a.is_published, a.published_at, a.approved_by_id = "published", True, utcnow(), user.id
    _retotal(a)
    db.commit()
    audit(db, "ASSESSMENT_PUBLISHED", "assessment", a.id, user=user, request=request)
    return _a_view(a)


@router.post("/assessments/{assessment_id}/close")
def close(assessment_id: int, request: Request, user: User = FacultyOnly, db: Session = Depends(get_db)):
    a = _own_assessment(db, user, assessment_id)
    if a.status != "published":
        raise HTTPException(409, "Only published assessments can be closed.")
    a.status, a.is_published = "closed", False
    for att in db.query(AssessmentAttempt).filter_by(assessment_id=a.id, status="in_progress"):
        svc.grade_attempt(db, att, status="auto_submitted")
    db.commit()
    audit(db, "ASSESSMENT_CLOSED", "assessment", a.id, user=user, request=request)
    return _a_view(a)


@router.delete("/assessments/{assessment_id}")
def delete_draft(assessment_id: int, user: User = FacultyOnly, db: Session = Depends(get_db)):
    a = _own_assessment(db, user, assessment_id)
    _require_draft(a)
    db.delete(a)
    db.commit()
    return {"status": "deleted"}


# ------------------------------------------------------------------ attempts, grading, integrity
def _scoped_student_ids(db: Session, user: User, subject_id: int) -> set:
    subject = perm.require_subject_faculty(db, user, subject_id)
    return {s.id for s in perm.students_in_subject_scope(db, subject, perm.faculty_of(db, user))}


@router.get("/assessments/{assessment_id}/attempts")
def attempts(assessment_id: int, user: User = FacultyOnly, db: Session = Depends(get_db)):
    a = _own_assessment(db, user, assessment_id)
    scope = _scoped_student_ids(db, user, a.subject_id)
    out = []
    for at in db.query(AssessmentAttempt).filter_by(assessment_id=a.id).order_by(AssessmentAttempt.id.desc()):
        if at.student_id not in scope:
            continue
        st = db.query(Student).filter_by(id=at.student_id).first()
        out.append({"id": at.id, "student": {"id": st.id, "roll_number": st.roll_number, "name": st.user.full_name},
                    "status": at.status, "score": at.total_score, "max": at.max_score, "percentage": at.percentage,
                    "violations": at.violation_count, "needs_review": at.needs_review,
                    "integrity_review_required": at.integrity_review_required, "grade_version": at.grade_version,
                    "completed_at": at.completed_at.isoformat() if at.completed_at else None})
    return out


def _own_attempt(db: Session, user: User, attempt_id: int) -> AssessmentAttempt:
    at = db.query(AssessmentAttempt).filter_by(id=attempt_id).first()
    if not at:
        raise perm.not_found("Attempt")
    a = at.assessment
    if a.kind == "practice":
        raise perm.not_found("Attempt")
    if at.student_id not in _scoped_student_ids(db, user, a.subject_id):
        raise perm.forbidden("This student is not in your class scope for the subject.")
    return at


@router.get("/attempts/{attempt_id}")
def attempt_detail(attempt_id: int, user: User = FacultyOnly, db: Session = Depends(get_db)):
    at = _own_attempt(db, user, attempt_id)
    rows = {r.question_id: r for r in at.answers}
    out = []
    for q in at.assessment.questions:
        r = rows.get(q.id)
        va = r.voice_answer if r else None
        out.append({"question": _q_full(q), "answer_text": r.answer_text if r else None,
                    "answer_json": r.answer_json if r else None, "score": r.score_awarded if r else 0,
                    "override": r.faculty_score_override if r else None, "feedback": r.ai_feedback if r else None,
                    "method": r.evaluation_method if r else None, "detail": r.evaluation_detail if r else None,
                    "needs_review": bool(r and r.needs_review and not r.reviewed_at), "answer_id": r.id if r else None,
                    "voice": {"transcript": va.transcript, "duration": va.duration_seconds, "engine": va.stt_engine,
                              "identified": va.identified_concepts, "missing": va.missing_concepts} if va else None})
    return {"id": at.id, "status": at.status, "score": at.total_score, "max": at.max_score, "grade_version": at.grade_version,
            "answers": out,
            "violations": [{"id": v.id, "type": v.violation_type, "at": v.timestamp.isoformat(), "severity": v.severity,
                            "review_status": v.review_status, "note": v.review_note, "detail": v.description}
                           for v in at.violations],
            "integrity_note": "Integrity events are browser signals only and are never used for automatic punishment; "
                              "a faculty member decides."}


@router.post("/answers/{answer_id}/grade")
def override_grade(answer_id: int, body: GradeOverride, request: Request, user: User = FacultyOnly,
                   db: Session = Depends(get_db)):
    row = db.query(StudentAnswer).filter_by(id=answer_id).first()
    if not row:
        raise perm.not_found("Answer")
    at = _own_attempt(db, user, row.attempt_id)
    q = row.question
    if body.score > q.marks:
        raise HTTPException(422, f"Score cannot exceed the question's {q.marks} marks.")
    if at.status == "in_progress":
        raise HTTPException(409, "The attempt is still in progress.")
    row.faculty_score_override, row.faculty_comment = body.score, body.comment
    row.reviewed_by_id, row.reviewed_at, row.needs_review = user.id, utcnow(), False
    db.commit()
    svc.recompute_after_override(db, at)
    audit(db, "GRADE_OVERRIDDEN", "student_answer", row.id, user=user, request=request,
          details={"attempt": at.id, "score": body.score})
    return {"attempt_score": at.total_score, "percentage": at.percentage, "grade_version": at.grade_version}


@router.post("/violations/{violation_id}/review")
def review_violation(violation_id: int, body: ViolationReview, request: Request, user: User = FacultyOnly,
                     db: Session = Depends(get_db)):
    v = db.query(AssessmentViolation).filter_by(id=violation_id).first()
    if not v:
        raise perm.not_found("Violation")
    at = _own_attempt(db, user, v.attempt_id)
    v.review_status, v.reviewed_by_id, v.reviewed_at, v.review_note = body.review_status, user.id, utcnow(), body.note
    if all(x.review_status != "pending" for x in at.violations):
        at.integrity_review_required = False
        at.integrity_status = "confirmed_concern" if any(x.review_status == "confirmed" for x in at.violations) else "cleared"
    db.commit()
    audit(db, "VIOLATION_REVIEWED", "violation", v.id, user=user, request=request, details={"status": body.review_status})
    return {"status": v.review_status, "integrity_status": at.integrity_status}


# ------------------------------------------------------------------ analytics (real data only)
@router.get("/analytics")
def analytics(subject_id: int, user: User = FacultyOnly, db: Session = Depends(get_db)):
    subject = perm.require_subject_faculty(db, user, subject_id)
    fac = perm.faculty_of(db, user)
    students = perm.students_in_subject_scope(db, subject, fac).all()
    ids = [s.id for s in students]
    topics = [t for u in subject.units for t in u.topics]
    m_rows = db.query(TopicMastery).filter(TopicMastery.subject_id == subject.id, TopicMastery.student_id.in_(ids or [-1])).all()
    by_topic, by_student = {}, {}
    for m in m_rows:
        by_topic.setdefault(m.topic_id, []).append(m)
        by_student.setdefault(m.student_id, []).append(m)
    topic_stats = []
    for t in topics:
        ms = by_topic.get(t.id, [])
        topic_stats.append({"topic_id": t.id, "title": t.title, "unit": t.unit.unit_number, "students": len(ms),
                            "avg_mastery": round(sum(m.mastery_score for m in ms) / len(ms), 1) if ms else None,
                            "struggling": sum(1 for m in ms if m.mastery_score < 50)})
    atts = db.query(AssessmentAttempt).join(Assessment).filter(
        Assessment.subject_id == subject.id, Assessment.kind == "daily", AssessmentAttempt.student_id.in_(ids or [-1]),
        AssessmentAttempt.status.in_(["completed", "auto_submitted"])).all()
    roster = []
    for s in students:
        ms = by_student.get(s.id, [])
        sa = [x for x in atts if x.student_id == s.id]
        roster.append({"student_id": s.id, "roll_number": s.roll_number, "name": s.user.full_name,
                       "attempts": len(sa), "avg_percentage": round(sum(x.percentage for x in sa) / len(sa), 1) if sa else None,
                       "avg_mastery": round(sum(m.mastery_score for m in ms) / len(ms), 1) if ms else None,
                       "needs_attention": bool(ms) and sum(m.mastery_score for m in ms) / len(ms) < 50})
    tids = [t.id for t in topics]
    miscs = db.query(Misconception.misconception_text, func.sum(Misconception.occurrence_count)).filter(
        Misconception.topic_id.in_(tids or [-1]), Misconception.student_id.in_(ids or [-1])
    ).group_by(Misconception.misconception_text).order_by(func.sum(Misconception.occurrence_count).desc()).limit(10).all()
    return {"subject": {"id": subject.id, "code": subject.code, "name": subject.name}, "students": len(ids),
            "attempts": len(atts),
            "avg_percentage": round(sum(x.percentage for x in atts) / len(atts), 1) if atts else None,
            "topics": topic_stats, "roster": roster,
            "common_misconceptions": [{"description": d, "occurrences": int(n)} for d, n in miscs],
            "pending_review": sum(1 for x in atts if x.needs_review),
            "pending_integrity": sum(1 for x in atts if x.integrity_review_required)}


# ------------------------------------------------------------------ teacher reviews
@router.post("/reviews", status_code=201)
def create_review(body: TeacherReviewIn, request: Request, user: User = FacultyOnly, db: Session = Depends(get_db)):
    st = db.query(Student).filter_by(id=body.student_id).first()
    if not st:
        raise perm.not_found("Student")
    perm.faculty_can_see_student(db, user, st, body.subject_id)
    fac = perm.faculty_of(db, user)
    r = TeacherReview(faculty_id=fac.id, student_id=st.id, subject_id=body.subject_id, unit_id=body.unit_id,
                      rating=body.rating, comment=body.comment, follow_up_status="open" if body.follow_up else "none")
    db.add(r)
    db.commit()
    audit(db, "TEACHER_REVIEW_CREATED", "teacher_review", r.id, user=user, request=request)
    return {"id": r.id, "follow_up_status": r.follow_up_status}


@router.get("/reviews")
def my_reviews(subject_id: Optional[int] = None, user: User = FacultyOnly, db: Session = Depends(get_db)):
    fac = perm.faculty_of(db, user)
    q = db.query(TeacherReview).filter_by(faculty_id=fac.id)
    if subject_id:
        q = q.filter_by(subject_id=subject_id)
    return [{"id": r.id, "student_id": r.student_id, "subject_id": r.subject_id, "rating": r.rating, "comment": r.comment,
             "follow_up_status": r.follow_up_status, "follow_up_note": r.follow_up_note,
             "created_at": r.created_at.isoformat()} for r in q.order_by(TeacherReview.id.desc()).limit(200)]


@router.patch("/reviews/{review_id}/follow-up")
def update_followup(review_id: int, body: FollowUpUpdate, user: User = FacultyOnly, db: Session = Depends(get_db)):
    fac = perm.faculty_of(db, user)
    r = db.query(TeacherReview).filter_by(id=review_id, faculty_id=fac.id).first()
    if not r:
        raise perm.not_found("Review")
    r.follow_up_status, r.follow_up_note = body.follow_up_status, body.follow_up_note
    r.follow_up_at = utcnow() if body.follow_up_status == "done" else None
    db.commit()
    return {"status": r.follow_up_status}


@router.get("/students/{student_id}")
def student_detail(student_id: int, subject_id: int, user: User = FacultyOnly, db: Session = Depends(get_db)):
    st = db.query(Student).filter_by(id=student_id).first()
    if not st:
        raise perm.not_found("Student")
    subject = perm.faculty_can_see_student(db, user, st, subject_id)
    ms = db.query(TopicMastery).filter_by(student_id=st.id, subject_id=subject.id).all()
    atts = db.query(AssessmentAttempt).join(Assessment).filter(
        AssessmentAttempt.student_id == st.id, Assessment.subject_id == subject.id, Assessment.kind == "daily").all()
    return {"student": {"id": st.id, "roll_number": st.roll_number, "name": st.user.full_name},
            "mastery": [{"topic_id": m.topic_id, "score": m.mastery_score, "trend": m.trend, "attempts": m.attempts_count}
                        for m in ms],
            "attempts": [{"id": x.id, "assessment": x.assessment.title, "percentage": x.percentage, "status": x.status}
                         for x in atts]}
