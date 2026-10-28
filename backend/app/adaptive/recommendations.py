"""Student-specific recommendations built only from the student's real mastery data and the
materials they are authorised to read."""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.auth import permissions as perm
from app.models.models import (
    LearningMaterial, Misconception, Recommendation, RevisionQueue, Student, Subject, Topic, TopicMastery,
)


def _materials_for(db: Session, student: Student, topic_id: int) -> List[LearningMaterial]:
    mats = db.query(LearningMaterial).filter(LearningMaterial.topic_id == topic_id).all()
    return [m for m in mats if perm.material_visible_to_student(db, m, student)]


def summary_sentence(strengths: List[Dict], weaknesses: List[Dict], mats: List[LearningMaterial], diff: str) -> str:
    if strengths and weaknesses:
        s, w = strengths[0], weaknesses[0]
        rev = f" Review {mats[0].title}" if mats else " Review your notes"
        return (f"You are strong in {s['topic_name']} ({s['mastery_score']:.0f}%) but need more practice with "
                f"{w['topic_name']} ({w['mastery_score']:.0f}%).{rev} and attempt five {diff}-difficulty questions.")
    if weaknesses:
        w = weaknesses[0]
        return f"Focus on {w['topic_name']} ({w['mastery_score']:.0f}%): revise your notes, then attempt five {diff}-difficulty questions."
    if strengths:
        return f"Great work on {strengths[0]['topic_name']} ({strengths[0]['mastery_score']:.0f}%). Try some harder questions to keep growing."
    return "Complete your first assessment and your personalised plan will appear here."


def regenerate(db: Session, student: Student) -> Dict[str, Any]:
    from app.adaptive.mastery_engine import adaptive_mastery_engine as eng
    prof = eng.profile(db, student.id)
    db.query(Recommendation).filter(Recommendation.student_id == student.id, Recommendation.generated_by == "adaptive",
                                    Recommendation.status == "pending").delete()
    db.query(RevisionQueue).filter(RevisionQueue.student_id == student.id, RevisionQueue.completed.is_(False)).delete()
    repeated = {}
    for m in db.query(Misconception).filter(Misconception.student_id == student.id, Misconception.is_resolved.is_(False)):
        if (m.occurrence_count or 1) >= 2:
            repeated.setdefault(m.topic_id, []).append(m.misconception_text)

    queue_candidates = []
    for t in prof["topics"]:
        reasons, prio = [], 0
        if t["mastery_score"] < 60:
            reasons.append(f"mastery is {t['mastery_score']:.0f}%")
            prio += 100 - t["mastery_score"]
        if t["trend"] == "declining":
            reasons.append("recent scores are dropping")
            prio += 25
        if t["topic_id"] in repeated:
            reasons.append("a misconception keeps repeating")
            prio += 30
        if reasons:
            queue_candidates.append((prio, t, reasons))
    queue_candidates.sort(key=lambda x: -x[0])

    for order, (prio, t, reasons) in enumerate(queue_candidates[:8], 1):
        mats = _materials_for(db, student, t["topic_id"])
        mat_ids = [m.id for m in mats[:3]]
        why = "Because " + " and ".join(reasons) + "."
        db.add(RevisionQueue(student_id=student.id, topic_id=t["topic_id"], priority_order=order,
                             estimated_time_minutes=15, action_type="concept_revision", reason=why,
                             material_ids_json=mat_ids))
        diff = "easy" if t["mastery_score"] < 60 else t["current_difficulty"]
        db.add(Recommendation(
            student_id=student.id, subject_id=t["subject_id"], topic_id=t["topic_id"],
            title=f"Revise {t['topic_name']}", reason=why, activity_type="revise_concept",
            priority="high" if t["mastery_score"] < 50 else "medium", estimated_minutes=15, generated_by="adaptive",
            description=(f"Read: {', '.join(m.title for m in mats[:2])}. " if mats else "Review your class notes. ") +
                        f"Then attempt five {diff}-difficulty practice questions.",
            material_ids_json=mat_ids))
    for t in prof["strengths"][:2]:
        if t["current_difficulty"] == "hard":
            db.add(Recommendation(
                student_id=student.id, subject_id=t["subject_id"], topic_id=t["topic_id"], generated_by="adaptive",
                title=f"Challenge yourself: {t['topic_name']}", activity_type="scenario_challenge", priority="low",
                estimated_minutes=20, reason=f"Your mastery is {t['mastery_score']:.0f}%.",
                description="Try hard-difficulty scenario and analytical questions to push beyond recall."))
    db.commit()
    diff = prof["weaknesses"][0]["current_difficulty"] if prof["weaknesses"] else "medium"
    mats = _materials_for(db, student, prof["weaknesses"][0]["topic_id"]) if prof["weaknesses"] else []
    return {"summary": summary_sentence(prof["strengths"], prof["weaknesses"], mats, "easy" if prof["weaknesses"] else diff),
            "revision_items": len(queue_candidates[:8])}
