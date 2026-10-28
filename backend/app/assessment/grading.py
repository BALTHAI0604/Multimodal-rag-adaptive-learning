"""Server-side grading of every question type.  The client only ever sends raw answers."""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any, Dict, List, Optional

from app.ai.grader import GradeResult, evaluate_text_answer
from app.assessment.types import OBJECTIVE_TYPES
from app.config import settings
from app.models.models import Question


def opaque(attempt_id: int, qid: int, kind: str, idx) -> str:
    """Per-attempt opaque id so answer positions / row ids can't leak the key."""
    mac = hmac.new(settings.hmac_key(), f"{attempt_id}:{qid}:{kind}:{idx}".encode(), hashlib.sha256)
    return mac.hexdigest()[:10]


def norm_text(s: str) -> str:
    s = re.sub(r"[^\w\s.]", " ", (s or "").lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip().rstrip(".")


def parse_number(s: str) -> Optional[float]:
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace("−", "-")
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?(?:\s*/\s*[-+]?\d*\.?\d+)?", t)
    if not m:
        return None
    tok = m.group(0)
    try:
        if "/" in tok:
            a, b = [x.strip() for x in tok.split("/")]
            return float(a) / float(b)
        return float(tok)
    except (ValueError, ZeroDivisionError):
        return None


def uses_options(q: Question) -> bool:
    return bool(q.options) and q.question_type not in {"fill_blank", "numerical", "match_following", "sequence",
                                                       "voice", "short_answer"}


def grade_question(q: Question, answer: Dict[str, Any], attempt_id: int, negative_marking: float = 0.0) -> GradeResult:
    """answer = {selected_option: opaque token | None, text: str | None, json: dict | None}"""
    marks = float(q.marks or 0)
    qt = q.question_type
    text = (answer.get("text") or "").strip()
    sel = answer.get("selected_option")
    data = answer.get("json") or {}

    # ---------------- option based (mcq / true_false / assertion_reason / scenario with options ...)
    if uses_options(q):
        if not sel:
            return GradeResult(0.0, marks, False, "Not answered.", "objective", False, {"answered": False})
        chosen = next((o for o in q.options if opaque(attempt_id, q.id, "opt", o.id) == sel), None)
        if chosen is None:
            return GradeResult(0.0, marks, False, "Invalid option selected.", "objective", False, {"answered": False})
        if chosen.is_correct:
            return GradeResult(marks, marks, True, "Correct. " + (chosen.explanation or q.explanation or ""),
                               "objective", False, {"answered": True, "selected_option_id": chosen.id})
        penalty = round(marks * negative_marking, 2) if negative_marking else 0.0
        detail = {"answered": True, "selected_option_id": chosen.id, "penalty": penalty}
        if chosen.misconception:
            detail["misconception"] = chosen.misconception
        return GradeResult(-penalty, marks, False, "Incorrect. " + (chosen.explanation or q.explanation or ""),
                           "objective", False, detail)

    # ---------------- fill in the blank
    if qt == "fill_blank":
        accepted = [norm_text(a) for a in (q.accepted_answers_json or []) if a]
        if not accepted:
            correct = next((o.option_text for o in q.options if o.is_correct), None)
            accepted = [norm_text(correct)] if correct else []
        if not text:
            return GradeResult(0.0, marks, False, "Not answered.", "objective", False, {"answered": False})
        if norm_text(text) in accepted:
            return GradeResult(marks, marks, True, "Correct.", "objective", False, {"answered": True})
        return GradeResult(0.0, marks, False, f"Incorrect. Expected: {', '.join((q.accepted_answers_json or accepted)[:3])}.",
                           "objective", False, {"answered": True})

    # ---------------- numerical
    if qt == "numerical":
        val = parse_number(text)
        if val is None:
            return GradeResult(0.0, marks, False, "Enter a number." if text else "Not answered.", "objective", False,
                               {"answered": bool(text)})
        target = q.numeric_answer
        if target is None:
            return GradeResult(0.0, marks, False, "No answer key set; awaiting faculty review.", "no-key", True, {})
        tol = q.numeric_tolerance if q.numeric_tolerance is not None else max(abs(target) * 1e-3, 1e-6)
        if abs(val - target) <= tol:
            return GradeResult(marks, marks, True, "Correct.", "objective", False, {"answered": True})
        return GradeResult(0.0, marks, False, f"Incorrect. The expected value is {target:g}.", "objective", False,
                           {"answered": True})

    # ---------------- match the following
    if qt == "match_following":
        pairs = q.match_pairs_json or []
        given = (data or {}).get("pairs") or {}
        if not given:
            return GradeResult(0.0, marks, False, "Not answered.", "objective", False, {"answered": False})
        correct = sum(1 for i in range(len(pairs)) if given.get(str(i)) == opaque(attempt_id, q.id, "right", i))
        frac = correct / max(1, len(pairs))
        score = round(marks * frac * 2) / 2.0
        return GradeResult(score, marks, correct == len(pairs), f"{correct} of {len(pairs)} pairs matched correctly.",
                           "objective", False, {"answered": True, "correct_pairs": correct})

    # ---------------- sequence
    if qt == "sequence":
        items = q.sequence_items_json or []
        order = (data or {}).get("order") or []
        if not order:
            return GradeResult(0.0, marks, False, "Not answered.", "objective", False, {"answered": False})
        pos = {opaque(attempt_id, q.id, "seq", i): i for i in range(len(items))}
        seq = [pos.get(t, -1) for t in order]
        n = len(items)
        if n < 2 or len(seq) != n or -1 in seq:
            return GradeResult(0.0, marks, False, "The sequence was incomplete.", "objective", False, {"answered": True})
        total = n * (n - 1) // 2
        concord = sum(1 for i in range(n) for j in range(i + 1, n) if seq[i] < seq[j])
        frac = max(0.0, 2 * (concord / total) - 1)                      # random order scores ~0
        score = round(marks * frac * 2) / 2.0
        return GradeResult(score, marks, seq == sorted(seq), "Correct order." if seq == sorted(seq)
                           else "Some steps are in the wrong order.", "objective", False, {"answered": True})

    # ---------------- rubric graded (short answer / analytical / scenario / application / voice)
    res = evaluate_text_answer(q.question_text, text, marks, q.rubric_json, q.answer_key_text or q.sample_ideal_answer)
    if q.explanation and res.score < marks:
        res.feedback += f" Key idea: {q.explanation}"
    return res


def is_objective(q: Question) -> bool:
    return uses_options(q) or q.question_type in OBJECTIVE_TYPES
