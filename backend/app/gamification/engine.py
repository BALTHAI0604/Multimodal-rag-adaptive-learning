"""XP, levels, streaks, badges.  Everything is awarded from server-verified activity and every reward
is idempotent (unique (student, source_type, source_ref) in the DB)."""
from __future__ import annotations

import datetime
import secrets
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import (
    Assessment, AssessmentAttempt, Badge, Student, StudentAnswer, StudentBadge, TopicMastery, XPTransaction,
    AIConversation, Question, utcnow,
)

LEVELS = [(1, "Novice Scholar", 0), (2, "Concept Explorer", 200), (3, "Knowledge Seeker", 500),
          (4, "Analytical Thinker", 1000), (5, "Academic Master", 1800), (6, "Cognitive Vanguard", 3000)]

BADGES = [
    ("first_steps", "First Steps", "Complete your first assessment.", "Complete 1 assessment (any score).", "Footprints", 20),
    ("perfect_score", "Perfect Score", "Score 100% on an assessment.", "Reach 100% on a completed assessment.", "Star", 50),
    ("7_day_learner", "7-Day Learner", "Learn seven days in a row.", "A streak of 7 consecutive days.", "Flame", 70),
    ("30_day_learner", "30-Day Learner", "Learn thirty days in a row.", "A streak of 30 consecutive days.", "Flame", 200),
    ("concept_master", "Concept Master", "Master five topics.", "Mastery ≥ 80% on at least 5 topics.", "Award", 100),
    ("voice_scholar", "Voice Scholar", "Speak your way to a high score.", "Score ≥ 70% on 3 voice answers.", "Mic", 75),
    ("analytical_thinker", "Analytical Thinker", "Handle higher-order questions.", "Score ≥ 70% on 5 analyze/evaluate/create questions.", "Brain", 80),
    ("curious_mind", "Curious Mind", "Ask your AI companion.", "Ask 10 questions to the learning companion.", "Compass", 40),
    ("comeback", "Comeback Kid", "Improve a weak topic.", "Raise a topic's mastery by 20+ points over attempts.", "TrendingUp", 60),
]


def ensure_badges(db: Session) -> None:
    for code, name, desc, crit, icon, xp in BADGES:
        b = db.query(Badge).filter_by(code=code).first()
        if not b:
            db.add(Badge(code=code, name=name, description=desc, criteria=crit, icon=icon, xp_bonus=xp))
        else:
            b.criteria = b.criteria or crit
    db.commit()


def level_for(xp: int):
    cur = LEVELS[0]
    for lv in LEVELS:
        if xp >= lv[2]:
            cur = lv
    nxt = next((l for l in LEVELS if l[2] > xp), None)
    return cur, nxt


def student_today(student: Student) -> datetime.date:
    tz = ZoneInfo(student.timezone or settings.APP_TIMEZONE)
    return datetime.datetime.now(tz).date()


def award_xp(db: Session, student: Student, amount: int, source_type: str, source_ref: str, description: str) -> Dict[str, Any]:
    """Idempotent: a second call with the same (source_type, source_ref) awards nothing."""
    if amount <= 0:
        return {"awarded": 0, "duplicate": False}
    try:
        with db.begin_nested():
            db.add(XPTransaction(student_id=student.id, amount=amount, source_type=source_type,
                                 source_ref=str(source_ref), description=description[:250]))
            db.flush()
    except IntegrityError:
        return {"awarded": 0, "duplicate": True}
    student.total_xp = (student.total_xp or 0) + amount
    old = student.current_level or 1
    student.current_level = level_for(student.total_xp)[0][0]
    db.commit()
    return {"awarded": amount, "duplicate": False, "leveled_up": student.current_level > old}


def update_streak(db: Session, student: Student) -> Dict[str, Any]:
    """Streak counts local calendar days with >=1 completed assessment.
    same day: unchanged | next day: +1 | gap: reset to 1."""
    today = student_today(student)
    last = student.streak_last_date
    if last == today:
        pass
    elif last is not None and (today - last).days == 1:
        student.streak_days = (student.streak_days or 0) + 1
    else:
        student.streak_days = 1
    student.streak_last_date = today
    student.longest_streak = max(student.longest_streak or 0, student.streak_days)
    db.commit()
    return {"current_streak": student.streak_days, "longest_streak": student.longest_streak}


def effective_streak(student: Student) -> int:
    """What to show today: a streak whose last day is before yesterday has already lapsed."""
    if not student.streak_last_date:
        return 0
    return student.streak_days if (student_today(student) - student.streak_last_date).days <= 1 else 0


def _eligible(db: Session, st: Student, code: str) -> Optional[str]:
    if code == "first_steps":
        return "Completed your first assessment." if db.query(AssessmentAttempt).filter(
            AssessmentAttempt.student_id == st.id, AssessmentAttempt.status.in_(["completed", "auto_submitted"])).count() >= 1 else None
    if code == "perfect_score":
        return "Scored 100%." if db.query(AssessmentAttempt).filter(
            AssessmentAttempt.student_id == st.id, AssessmentAttempt.percentage >= 100, AssessmentAttempt.status.in_(["completed", "auto_submitted"])).first() else None
    if code == "7_day_learner":
        return "7-day streak." if (st.streak_days or 0) >= 7 else None
    if code == "30_day_learner":
        return "30-day streak." if (st.streak_days or 0) >= 30 else None
    if code == "concept_master":
        return "Mastered 5 topics." if db.query(TopicMastery).filter(TopicMastery.student_id == st.id, TopicMastery.mastery_score >= 80).count() >= 5 else None
    if code == "voice_scholar":
        n = (db.query(StudentAnswer).join(Question, Question.id == StudentAnswer.question_id)
             .filter(StudentAnswer.student_id == st.id, Question.question_type == "voice", StudentAnswer.max_score > 0)
             .all())
        return "Three strong spoken answers." if sum(1 for a in n if a.score_awarded >= 0.7 * a.max_score) >= 3 else None
    if code == "analytical_thinker":
        rows = (db.query(StudentAnswer).join(Question, Question.id == StudentAnswer.question_id)
                .filter(StudentAnswer.student_id == st.id, Question.bloom_level.in_(["analyze", "evaluate", "create"]),
                        StudentAnswer.max_score > 0).all())
        return "Strong higher-order answers." if sum(1 for a in rows if a.score_awarded >= 0.7 * a.max_score) >= 5 else None
    if code == "curious_mind":
        return "Asked 10 questions." if db.query(AIConversation).filter(AIConversation.student_id == st.id).count() >= 10 else None
    if code == "comeback":
        for m in db.query(TopicMastery).filter(TopicMastery.student_id == st.id):
            sc = m.last_scores_json or []
            if len(sc) >= 3 and max(sc[1:]) - min(sc[:2]) >= 20 and sc[-1] >= max(sc) - 5:
                return "Big improvement on a topic."
        return None
    return None


def check_badges(db: Session, student: Student) -> List[Dict[str, Any]]:
    ensure_badges(db)
    have = {sb.badge_id for sb in db.query(StudentBadge).filter_by(student_id=student.id)}
    new = []
    for b in db.query(Badge).all():
        if b.id in have:
            continue
        reason = _eligible(db, student, b.code)
        if not reason:
            continue
        try:
            with db.begin_nested():
                db.add(StudentBadge(student_id=student.id, badge_id=b.id, reason=reason))
                db.flush()
        except IntegrityError:
            continue
        db.commit()
        bonus = award_xp(db, student, b.xp_bonus, "badge", b.code, f"Badge: {b.name}")
        new.append({"code": b.code, "name": b.name, "description": b.description, "icon": b.icon, "xp": bonus["awarded"]})
    return new


def reward_attempt(db: Session, student: Student, attempt: AssessmentAttempt, assessment: Assessment,
                   mastery: Dict[str, Any]) -> Dict[str, Any]:
    pct = attempt.percentage or 0
    base = 20 + round(pct * 0.3) if assessment.kind == "daily" else 10 + round(pct * 0.1)
    xp = {"base": award_xp(db, student, base, "assessment", f"attempt-{attempt.id}", f"Completed {assessment.title}")}
    if assessment.kind == "practice":
        today = student_today(student)
        n = db.query(XPTransaction).filter(XPTransaction.student_id == student.id, XPTransaction.source_type == "assessment",
                                           XPTransaction.description.like("Completed Practice%")).count()
    voice = db.query(StudentAnswer).join(Question, Question.id == StudentAnswer.question_id).filter(
        StudentAnswer.attempt_id == attempt.id, Question.question_type == "voice", StudentAnswer.max_score > 0).all()
    if voice and all(v.score_awarded >= 0.7 * v.max_score for v in voice):
        xp["voice"] = award_xp(db, student, 10, "voice_bonus", f"attempt-{attempt.id}", "Strong spoken answer")
    if mastery and mastery.get("new_mastery", 0) >= 80 and (mastery.get("old_mastery") or 0) < 80:
        xp["milestone"] = award_xp(db, student, 30, "mastery_milestone", f"topic-{mastery['topic_id']}", "Topic mastery milestone")
    streak = update_streak(db, student)
    if streak["current_streak"] in (7, 30):
        xp["streak"] = award_xp(db, student, 25 if streak["current_streak"] == 7 else 100, "streak_bonus",
                                f"streak-{streak['current_streak']}-{student_today(student).isoformat()}", f"{streak['current_streak']}-day streak")
    badges = check_badges(db, student)
    cur, nxt = level_for(student.total_xp)
    return {"xp_awarded": sum(v.get("awarded", 0) for v in xp.values()), "total_xp": student.total_xp,
            "level": cur[0], "level_title": cur[1], "streak": streak, "badges_unlocked": badges}


def new_share_token() -> str:
    return secrets.token_urlsafe(24)
