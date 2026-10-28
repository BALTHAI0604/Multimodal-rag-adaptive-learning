"""Topic mastery from real, persisted attempts.

mastery_n = (1-a_n)*mastery_{n-1} + a_n*score_n,  a_n = max(0.35, 1/n)   (running mean first, then EMA)
Practice attempts count with half weight so memorising a bank cannot inflate mastery.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.models import (
    Assessment, AssessmentAttempt, Misconception, Question, StudentAnswer, Subject, Topic, TopicMastery, Unit, utcnow,
)


def difficulty_for(mastery: float, trend: str) -> str:
    d = "easy" if mastery < 60 else ("medium" if mastery < 80 else "hard")
    if trend == "declining" and d == "hard":
        d = "medium"
    return d


def _trend(scores: List[float]) -> str:
    if len(scores) < 2:
        return "insufficient_data"
    recent = sum(scores[-3:]) / len(scores[-3:])
    prev = scores[-6:-3] or scores[:-1][-3:]
    prev_avg = sum(prev) / len(prev)
    delta = recent - prev_avg
    return "improving" if delta > 5 else ("declining" if delta < -5 else "stable")


class AdaptiveMasteryEngine:
    @staticmethod
    def update_after_attempt(db: Session, attempt: AssessmentAttempt) -> Dict[str, Any]:
        a = db.query(Assessment).filter(Assessment.id == attempt.assessment_id).first()
        if not a:
            return {}
        weight = 0.5 if a.kind == "practice" else 1.0
        m = db.query(TopicMastery).filter_by(student_id=attempt.student_id, topic_id=a.topic_id).first()
        old = m.mastery_score if m else None
        if m is None:
            m = TopicMastery(student_id=attempt.student_id, subject_id=a.subject_id, topic_id=a.topic_id,
                             mastery_score=0.0, attempts_count=0, total_questions_attempted=0, correct_answers=0)
            db.add(m)
        n = (m.attempts_count or 0) + 1
        pct = float(attempt.percentage or 0)
        alpha = max(0.35, 1.0 / n) * weight
        m.mastery_score = round(pct if n == 1 else (1 - alpha) * m.mastery_score + alpha * pct, 1)
        scores = list(m.last_scores_json or []) + [round(pct, 1)]
        m.last_scores_json = scores[-10:]
        m.attempts_count = n
        m.avg_score = round(sum(scores) / len(scores), 1)
        m.recent_avg = round(sum(scores[-3:]) / len(scores[-3:]), 1)
        m.trend = _trend(scores)
        answers = db.query(StudentAnswer).filter(StudentAnswer.attempt_id == attempt.id).all()
        m.total_questions_attempted = (m.total_questions_attempted or 0) + len(answers)
        m.correct_answers = (m.correct_answers or 0) + sum(1 for x in answers if x.is_correct)
        m.current_difficulty = difficulty_for(m.mastery_score, m.trend)
        m.completion_status = "mastered" if (m.mastery_score >= 80 and n >= 2) else "in_progress"
        m.last_updated = utcnow()
        db.commit()
        return {"topic_id": a.topic_id, "old_mastery": old, "new_mastery": m.mastery_score, "trend": m.trend,
                "next_difficulty": m.current_difficulty, "attempts": n, "attempt_percentage": round(pct, 1),
                "completion_status": m.completion_status}

    @staticmethod
    def record_misconceptions(db: Session, attempt: AssessmentAttempt, details: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        a = db.query(Assessment).filter(Assessment.id == attempt.assessment_id).first()
        out = []
        for d in details:
            text = (d.get("misconception") or "").strip()
            if not text:
                continue
            row = db.query(Misconception).filter_by(student_id=attempt.student_id, topic_id=a.topic_id,
                                                    misconception_text=text).first()
            if row:
                row.occurrence_count = (row.occurrence_count or 1) + 1
                row.last_seen_at = utcnow()
                row.is_resolved = False
            else:
                row = Misconception(student_id=attempt.student_id, topic_id=a.topic_id, misconception_text=text,
                                    context_question_id=d.get("question_id"),
                                    corrective_explanation=d.get("explanation") or "Review this idea in your notes.")
                db.add(row)
            out.append({"misconception": text, "explanation": row.corrective_explanation,
                        "repeated": (row.occurrence_count or 1) >= 2})
        db.commit()
        return out

    @staticmethod
    def profile(db: Session, student_id: int) -> Dict[str, Any]:
        rows = db.query(TopicMastery).filter(TopicMastery.student_id == student_id).all()
        items = []
        for r in rows:
            t = db.query(Topic).filter(Topic.id == r.topic_id).first()
            s = db.query(Subject).filter(Subject.id == r.subject_id).first()
            items.append({"topic_id": r.topic_id, "topic_name": t.title if t else "Topic", "subject_id": r.subject_id,
                          "subject_name": s.name if s else "", "subject_code": s.code if s else "",
                          "mastery_score": r.mastery_score, "avg_score": r.avg_score, "recent_avg": r.recent_avg,
                          "attempts": r.attempts_count, "trend": r.trend, "current_difficulty": r.current_difficulty,
                          "completion_status": r.completion_status})
        strengths = sorted([i for i in items if i["mastery_score"] >= 75], key=lambda x: -x["mastery_score"])
        weaknesses = sorted([i for i in items if i["mastery_score"] < 60], key=lambda x: x["mastery_score"])
        moderate = [i for i in items if 60 <= i["mastery_score"] < 75]
        return {"topics": items, "strengths": strengths, "weaknesses": weaknesses, "moderate": moderate}

    @staticmethod
    def unit_progress(db: Session, student_id: int) -> List[Dict[str, Any]]:
        out = []
        masteries = {m.topic_id: m for m in db.query(TopicMastery).filter_by(student_id=student_id)}
        subj_ids = {m.subject_id for m in masteries.values()}
        for u in db.query(Unit).filter(Unit.subject_id.in_(subj_ids or {-1})).all():
            tids = [t.id for t in u.topics]
            have = [masteries[t].mastery_score for t in tids if t in masteries]
            out.append({"unit_id": u.id, "subject_id": u.subject_id, "unit": f"Unit {u.unit_number}: {u.title}",
                        "topics_total": len(tids), "topics_attempted": len(have),
                        "average_mastery": round(sum(have) / len(have), 1) if have else None})
        return out


adaptive_mastery_engine = AdaptiveMasteryEngine()


def rebuild_topic(db: Session, student_id: int, topic_id: int) -> None:
    """Recompute a student's mastery for one topic from all completed attempts (used after faculty grade corrections)."""
    db.query(TopicMastery).filter_by(student_id=student_id, topic_id=topic_id).delete()
    db.commit()
    rows = (db.query(AssessmentAttempt).join(Assessment, Assessment.id == AssessmentAttempt.assessment_id)
            .filter(AssessmentAttempt.student_id == student_id, Assessment.topic_id == topic_id,
                    AssessmentAttempt.status.in_(["completed", "auto_submitted"]))
            .order_by(AssessmentAttempt.completed_at).all())
    for att in rows:
        AdaptiveMasteryEngine.update_after_attempt(db, att)
