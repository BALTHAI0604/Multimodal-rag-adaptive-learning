"""Attempt lifecycle: start, answer saving, grading, finalising.  All scoring is server-side."""
from __future__ import annotations

import datetime
import random
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.adaptive import recommendations
from app.adaptive.mastery_engine import adaptive_mastery_engine
from app.assessment.grading import grade_question, opaque
from app.blockchain import service as chain
from app.config import settings
from app.gamification import engine as gami
from app.models.models import (
    Assessment, AssessmentAttempt, Question, Student, StudentAnswer, VoiceAnswer, utcnow,
)

log = __import__("logging").getLogger("attempts")


def answers_released(a: Assessment, now: Optional[datetime.datetime] = None) -> bool:
    now = now or utcnow()
    if a.kind == "practice":
        return True
    if a.answers_release_at:
        return now >= a.answers_release_at
    if a.end_time:
        return now >= a.end_time
    return a.status == "closed"


def release_time(a: Assessment) -> Optional[datetime.datetime]:
    return a.answers_release_at or a.end_time


def ordered_questions(a: Assessment, attempt: AssessmentAttempt) -> List[Question]:
    qs = list(a.questions)
    if a.shuffle_questions:
        random.Random(attempt.seed).shuffle(qs)
    return qs


def student_view(q: Question, attempt: AssessmentAttempt, shuffle_options: bool) -> Dict[str, Any]:
    """What a student may see.  No keys, rubrics, explanations or accepted answers; ids are opaque."""
    rng = random.Random(attempt.seed * 100003 + q.id)
    d: Dict[str, Any] = {"id": q.id, "question_type": q.question_type, "question_text": q.question_text, "marks": q.marks,
                         "bloom_level": q.bloom_level, "difficulty": q.difficulty, "options": []}
    if q.options and q.question_type not in {"fill_blank", "numerical", "match_following", "sequence", "voice", "short_answer"}:
        opts = [{"id": opaque(attempt.id, q.id, "opt", o.id), "text": o.option_text} for o in q.options]
        if shuffle_options and q.question_type not in ("true_false", "assertion_reason"):
            rng.shuffle(opts)
        d["options"] = opts
    if q.question_type == "assertion_reason":
        d["assertion_text"], d["reason_text"] = q.assertion_text, q.reason_text
    if q.question_type == "match_following":
        pairs = q.match_pairs_json or []
        d["match_left"] = [{"id": str(i), "text": p["left"]} for i, p in enumerate(pairs)]
        right = [{"id": opaque(attempt.id, q.id, "right", i), "text": p["right"]} for i, p in enumerate(pairs)]
        rng.shuffle(right)
        d["match_right"] = right
    if q.question_type == "sequence":
        items = [{"id": opaque(attempt.id, q.id, "seq", i), "text": it["text"]} for i, it in enumerate(q.sequence_items_json or [])]
        rng.shuffle(items)
        d["sequence_items"] = items
    if q.question_type == "voice":
        d.update({"voice_guidance": q.voice_guidance, "voice_prep_sec": q.voice_prep_sec or 10,
                  "voice_max_time_sec": q.voice_max_time_sec or 120})
    return d


def deadline_for(a: Assessment, st: Student, started: datetime.datetime) -> datetime.datetime:
    minutes = a.time_limit_minutes * (1 + (st.extra_time_percent or 0) / 100.0)
    dl = started + datetime.timedelta(minutes=minutes)
    if a.end_time and a.end_time < dl:
        dl = a.end_time
    return dl


def save_answer(db: Session, attempt: AssessmentAttempt, q: Question, selected: Optional[str], text: Optional[str],
                data: Optional[dict], mode: Optional[str]) -> StudentAnswer:
    row = db.query(StudentAnswer).filter_by(attempt_id=attempt.id, question_id=q.id).first()
    if row is None:
        row = StudentAnswer(attempt_id=attempt.id, question_id=q.id, student_id=attempt.student_id, max_score=q.marks)
        db.add(row)
    if selected is not None:
        row.answer_text = selected          # opaque token; replaced by real option text on grading
    if text is not None:
        row.answer_text = text
    if data is not None:
        row.answer_json = data
    if mode:
        row.input_mode = mode
    row.saved_at = utcnow()
    return row


def finalize_totals(db: Session, attempt: AssessmentAttempt) -> None:
    rows = db.query(StudentAnswer).filter_by(attempt_id=attempt.id).all()
    total = sum((r.faculty_score_override if r.faculty_score_override is not None else r.score_awarded) or 0 for r in rows)
    attempt.total_score = max(0.0, round(total, 2))
    attempt.max_score = round(sum(q.marks for q in attempt.assessment.questions), 2)
    attempt.percentage = round(100.0 * attempt.total_score / attempt.max_score, 1) if attempt.max_score else 0.0
    attempt.needs_review = any(r.needs_review and r.reviewed_at is None for r in rows)


def grade_attempt(db: Session, attempt: AssessmentAttempt, status: str = "completed") -> Dict[str, Any]:
    """Grade every question of the attempt (unanswered ones score 0), then update mastery, rewards, ledger."""
    a = attempt.assessment
    saved = {r.question_id: r for r in db.query(StudentAnswer).filter_by(attempt_id=attempt.id)}
    misc: List[Dict[str, Any]] = []
    for q in a.questions:
        row = saved.get(q.id) or save_answer(db, attempt, q, None, None, None, None)
        sel = None
        text = row.answer_text
        if q.options and q.question_type not in {"fill_blank", "numerical", "match_following", "sequence", "voice", "short_answer"}:
            sel, text = row.answer_text, None
        res = grade_question(q, {"selected_option": sel, "text": text, "json": row.answer_json}, attempt.id,
                             a.negative_marking or 0.0)
        row.score_awarded, row.max_score, row.is_correct = res.score, q.marks, res.is_correct
        row.ai_feedback, row.evaluation_method, row.evaluation_detail = res.feedback, res.method, res.detail
        row.needs_review, row.evaluated_at = res.needs_review, utcnow()
        if sel:
            chosen = next((o for o in q.options if opaque(attempt.id, q.id, "opt", o.id) == sel), None)
            if chosen:
                row.selected_option_id, row.answer_text = chosen.id, chosen.option_text
        if res.detail.get("misconception") and not res.is_correct:
            misc.append({"misconception": res.detail["misconception"], "question_id": q.id,
                         "explanation": q.explanation or ""})
        for m in res.detail.get("misconceptions", []) or []:
            misc.append({**m, "question_id": q.id})
        va = db.query(VoiceAnswer).filter_by(student_answer_id=row.id).first() if row.id else None
        if va and q.question_type == "voice":
            va.identified_concepts, va.missing_concepts = res.detail.get("identified"), res.detail.get("missing")
            va.detected_misconceptions = res.detail.get("misconceptions")
            va.conceptual_correctness_score = res.detail.get("coverage_pct", 0)
            va.ai_evaluation_json = {"method": res.method, "score": res.score, "max": q.marks}
    db.flush()
    attempt.status, attempt.completed_at = status, utcnow()
    finalize_totals(db, attempt)
    db.commit()

    student = db.query(Student).filter_by(id=attempt.student_id).first()
    mastery = adaptive_mastery_engine.update_after_attempt(db, attempt)
    misconceptions = adaptive_mastery_engine.record_misconceptions(db, attempt, misc)
    plan = recommendations.regenerate(db, student)
    rewards = gami.reward_attempt(db, student, attempt, a, mastery)
    record = chain.anchor_attempt(db, attempt)
    return {"mastery": mastery, "misconceptions": misconceptions, "plan": plan, "rewards": rewards,
            "ledger": {"record_ref": record.record_ref, "status": record.status, "error": record.error_message}}


def recompute_after_override(db: Session, attempt: AssessmentAttempt) -> None:
    finalize_totals(db, attempt)
    attempt.grade_version = (attempt.grade_version or 1) + 1
    db.commit()
    adaptive_mastery_engine_rebuild(db, attempt)
    chain.anchor_attempt(db, attempt)


def adaptive_mastery_engine_rebuild(db: Session, attempt: AssessmentAttempt) -> None:
    from app.adaptive.mastery_engine import rebuild_topic
    rebuild_topic(db, attempt.student_id, attempt.assessment.topic_id)
    st = db.query(Student).filter_by(id=attempt.student_id).first()
    recommendations.regenerate(db, st)
