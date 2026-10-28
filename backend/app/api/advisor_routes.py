"""Advisor: only students assigned to this advisor."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import permissions as perm
from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.models import (
    AdvisorReview, Assessment, AssessmentAttempt, Misconception, Recommendation, Student, Subject, TeacherReview,
    Topic, TopicMastery, User, utcnow,
)
from app.schemas.schemas import AdvisorReviewIn, InterventionUpdate
from app.security.audit import audit
from app.gamification import engine as gami

router = APIRouter(prefix="/advisor", tags=["Advisor"])
AdvisorOnly = Depends(require_roles("advisor"))


def _summary(db: Session, st: Student) -> dict:
    ms = db.query(TopicMastery).filter_by(student_id=st.id).all()
    avg = round(sum(m.mastery_score for m in ms) / len(ms), 1) if ms else None
    atts = db.query(AssessmentAttempt).filter(AssessmentAttempt.student_id == st.id,
                                               AssessmentAttempt.status.in_(["completed", "auto_submitted"])).all()
    open_iv = db.query(AdvisorReview).filter(AdvisorReview.student_id == st.id,
                                              AdvisorReview.intervention_status.in_(["open", "in_progress"])).count()
    weak = sum(1 for m in ms if m.mastery_score < 50)
    return {"student_id": st.id, "roll_number": st.roll_number, "name": st.user.full_name,
            "class_name": st.class_group.name if st.class_group else st.class_name, "avg_mastery": avg,
            "attempts": len(atts), "weak_topics": weak, "streak": gami.effective_streak(st),
            "open_interventions": open_iv, "flag": (avg is not None and avg < 50) or weak >= 2,
            "flag_reason": "Average mastery below 50% or two or more weak topics" if ((avg is not None and avg < 50) or weak >= 2) else None}


@router.get("/dashboard")
def dashboard(user: User = AdvisorOnly, db: Session = Depends(get_db)):
    ids = perm.advisor_student_ids(db, user)
    studs = db.query(Student).filter(Student.id.in_(ids or {-1})).order_by(Student.roll_number).all()
    rows = [_summary(db, s) for s in studs]
    return {"total_students": len(rows), "flagged": sum(1 for r in rows if r["flag"]),
            "open_interventions": sum(r["open_interventions"] for r in rows), "students": rows}


@router.get("/students/{student_id}")
def student(student_id: int, user: User = AdvisorOnly, db: Session = Depends(get_db)):
    st = perm.require_advisor_student(db, user, student_id)
    ms = db.query(TopicMastery).filter_by(student_id=st.id).all()
    by_subject = {}
    for m in ms:
        by_subject.setdefault(m.subject_id, []).append(m)
    subs = {s.id: s for s in db.query(Subject).filter(Subject.id.in_(by_subject or {-1}))}
    return {**_summary(db, st),
            "subjects": [{"subject": subs[sid].name, "code": subs[sid].code,
                          "avg_mastery": round(sum(m.mastery_score for m in v) / len(v), 1),
                          "topics": [{"topic": db.query(Topic.title).filter_by(id=m.topic_id).scalar(),
                                      "mastery": m.mastery_score, "trend": m.trend} for m in v]}
                         for sid, v in by_subject.items() if sid in subs],
            "misconceptions": [{"text": x.misconception_text, "count": x.occurrence_count}
                               for x in db.query(Misconception).filter_by(student_id=st.id, is_resolved=False).limit(10)],
            "teacher_reviews": [{"subject": r.subject.name, "rating": r.rating, "comment": r.comment,
                                 "follow_up_status": r.follow_up_status, "at": r.created_at.isoformat()}
                                for r in db.query(TeacherReview).filter_by(student_id=st.id).order_by(TeacherReview.id.desc()).limit(20)],
            "advisor_reviews": [{"id": r.id, "observation": r.observation, "recommendation": r.recommendation,
                                 "intervention_required": r.intervention_required, "intervention_status": r.intervention_status,
                                 "intervention_plan": r.intervention_plan, "at": r.created_at.isoformat()}
                                for r in db.query(AdvisorReview).filter_by(student_id=st.id).order_by(AdvisorReview.id.desc()).limit(20)]}


@router.post("/reviews", status_code=201)
def create_review(body: AdvisorReviewIn, request: Request, user: User = AdvisorOnly, db: Session = Depends(get_db)):
    st = perm.require_advisor_student(db, user, body.student_id)
    r = AdvisorReview(advisor_id=user.id, student_id=st.id, observation=body.observation,
                      recommendation=body.recommendation, intervention_required=body.intervention_required,
                      intervention_plan=body.intervention_plan,
                      intervention_status="open" if body.intervention_required else "none")
    db.add(r)
    db.commit()
    audit(db, "ADVISOR_REVIEW_CREATED", "advisor_review", r.id, user=user, request=request)
    return {"id": r.id, "intervention_status": r.intervention_status}


@router.patch("/reviews/{review_id}/intervention")
def update_intervention(review_id: int, body: InterventionUpdate, user: User = AdvisorOnly, db: Session = Depends(get_db)):
    r = db.query(AdvisorReview).filter_by(id=review_id, advisor_id=user.id).first()
    if not r:
        raise perm.not_found("Review")
    perm.require_advisor_student(db, user, r.student_id)
    r.intervention_status = body.intervention_status
    if body.intervention_plan is not None:
        r.intervention_plan = body.intervention_plan
    r.updated_at = utcnow()
    db.commit()
    return {"intervention_status": r.intervention_status}
