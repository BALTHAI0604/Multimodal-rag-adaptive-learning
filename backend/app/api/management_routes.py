"""Management: aggregated, anonymised institutional analytics only. No student rows, names, emails or marks."""
from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.models import (
    Assessment, AssessmentAttempt, ClassGroup, Student, Subject, TopicMastery, Topic, Unit, User, LearningMaterial,
)
from app.security.audit import audit

router = APIRouter(prefix="/management", tags=["Management"])
MgmtOnly = Depends(require_roles("management"))
MIN_GROUP = 5            # groups smaller than this are suppressed so individuals cannot be inferred


def _build(db: Session, department: Optional[str], semester: Optional[int]) -> dict:
    sq = db.query(Student)
    if department:
        sq = sq.filter(Student.department == department)
    if semester:
        sq = sq.filter(Student.semester == semester)
    studs = sq.all()
    ids = {s.id for s in studs}
    by_dept = {}
    for s in studs:
        by_dept.setdefault(s.department, []).append(s.id)
    ms = db.query(TopicMastery).filter(TopicMastery.student_id.in_(ids or {-1})).all()
    atts = db.query(AssessmentAttempt).join(Assessment).filter(
        AssessmentAttempt.student_id.in_(ids or {-1}), Assessment.kind == "daily",
        AssessmentAttempt.status.in_(["completed", "auto_submitted"])).all()
    dept_rows = []
    for d, sids in sorted(by_dept.items()):
        dm = [m for m in ms if m.student_id in set(sids)]
        da = [a for a in atts if a.student_id in set(sids)]
        if len(sids) < MIN_GROUP:
            dept_rows.append({"department": d, "students": f"<{MIN_GROUP}", "suppressed": True})
            continue
        dept_rows.append({"department": d, "students": len(sids), "suppressed": False,
                          "avg_mastery": round(sum(m.mastery_score for m in dm) / len(dm), 1) if dm else None,
                          "avg_assessment_percentage": round(sum(a.percentage for a in da) / len(da), 1) if da else None,
                          "assessments_completed": len(da)})
    subj_rows = []
    for s in db.query(Subject).order_by(Subject.code):
        sm = [m for m in ms if m.subject_id == s.id]
        n = len({m.student_id for m in sm})
        if n and n < MIN_GROUP:
            subj_rows.append({"code": s.code, "name": s.name, "students": f"<{MIN_GROUP}", "suppressed": True})
        elif n:
            subj_rows.append({"code": s.code, "name": s.name, "students": n, "suppressed": False,
                              "avg_mastery": round(sum(m.mastery_score for m in sm) / len(sm), 1)})
    topic_stats = {}
    for m in ms:
        topic_stats.setdefault(m.topic_id, []).append(m)
    difficult = []
    for tid, v in topic_stats.items():
        if len({m.student_id for m in v}) >= MIN_GROUP:
            t = db.query(Topic).filter_by(id=tid).first()
            difficult.append({"topic": t.title, "subject": t.unit.subject.code,
                              "avg_mastery": round(sum(m.mastery_score for m in v) / len(v), 1),
                              "struggling_pct": round(100 * sum(1 for m in v if m.mastery_score < 50) / len(v), 1)})
    difficult.sort(key=lambda x: x["avg_mastery"])
    active = len({a.student_id for a in atts})
    return {"filters": {"department": department, "semester": semester},
            "kpis": {"students": len(studs), "students_with_assessments": active,
                     "assessments_completed": len(atts),
                     "avg_assessment_percentage": round(sum(a.percentage for a in atts) / len(atts), 1) if atts else None,
                     "avg_mastery": round(sum(m.mastery_score for m in ms) / len(ms), 1) if ms else None,
                     "materials_indexed": db.query(LearningMaterial).filter_by(status="indexed").count()},
            "departments": dept_rows, "subjects": subj_rows, "difficult_topics": difficult[:10],
            "privacy_note": f"Groups with fewer than {MIN_GROUP} students are suppressed. No individual data is exposed."}


@router.get("/analytics")
def analytics(department: Optional[str] = None, semester: Optional[int] = None, user: User = MgmtOnly,
              db: Session = Depends(get_db)):
    return _build(db, department, semester)


@router.get("/export.csv")
def export(request: Request, department: Optional[str] = None, semester: Optional[int] = None,
           user: User = MgmtOnly, db: Session = Depends(get_db)):
    data = _build(db, department, semester)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["department", "students", "avg_mastery", "avg_assessment_percentage", "assessments_completed"])
    for r in data["departments"]:
        w.writerow([r["department"], r["students"], r.get("avg_mastery", ""), r.get("avg_assessment_percentage", ""),
                    r.get("assessments_completed", "")])
    audit(db, "MANAGEMENT_EXPORT", "analytics", None, user=user, request=request)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="institution-summary.csv"'})
