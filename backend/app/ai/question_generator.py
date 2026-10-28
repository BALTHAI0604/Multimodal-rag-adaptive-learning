"""Question generation grounded in faculty-approved material.

Primary path: local LLM (Ollama) returns JSON; every question is validated and malformed ones are
dropped.  Fallback path (no LLM running): an extractive generator builds questions directly from
sentences in the material (cloze, definition match, ordered steps ...).  Each question records its
`generation_method` so faculty know what to double-check.  There are NO hard-coded subject questions.
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from app.ai.grader import STOP, _tokens, extract_keywords, rubric_from_key
from app.ai.llm_provider import LLMError, LLMUnavailable, llm_provider
from app.assessment.types import (
    ASSERTION_REASON_OPTIONS, BLOOM_LEVELS, DEFAULT_MARKS, DIFFICULTIES, QUESTION_TYPES, RUBRIC_TYPES,
)

log = logging.getLogger("qgen")

TYPE_BLOOM = {"mcq": "understand", "true_false": "remember", "fill_blank": "remember", "match_following": "understand",
              "sequence": "apply", "assertion_reason": "analyze", "short_answer": "understand", "analytical": "analyze",
              "scenario": "evaluate", "application": "apply", "numerical": "apply", "voice": "understand"}


def norm_q(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def content_hash(text: str) -> str:
    return hashlib.sha1(norm_q(text).encode()).hexdigest()


def voice_timing(marks: float, prep: Optional[int] = None, speak: Optional[int] = None) -> Dict[str, int]:
    return {"voice_prep_sec": prep if prep is not None else 10,
            "voice_max_time_sec": speak if speak is not None else int(min(300, max(60, marks * 60)))}


# ====================================================================== validation
def _s(v) -> str:
    return str(v).strip() if v is not None else ""


def validate_question(raw: Dict[str, Any], default_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Return a clean internal dict or None when the question is unusable."""
    if not isinstance(raw, dict):
        return None
    qt = _s(raw.get("type") or raw.get("question_type") or default_type).lower()
    qt = {"multiple_choice": "mcq", "truefalse": "true_false", "match": "match_following", "fill_in_the_blank": "fill_blank",
          "assertion_and_reason": "assertion_reason", "short": "short_answer", "numeric": "numerical"}.get(qt, qt)
    text = _s(raw.get("question") or raw.get("question_text"))
    if qt not in QUESTION_TYPES or len(text) < 10:
        return None
    bloom = _s(raw.get("bloom_level") or raw.get("bloom")).lower()
    bloom = bloom if bloom in BLOOM_LEVELS else TYPE_BLOOM[qt]
    diff = _s(raw.get("difficulty")).lower()
    diff = diff if diff in DIFFICULTIES else "medium"
    try:
        marks = float(raw.get("marks", DEFAULT_MARKS[qt]))
    except (TypeError, ValueError):
        marks = float(DEFAULT_MARKS[qt])
    marks = marks if 0 < marks <= 20 else float(DEFAULT_MARKS[qt])
    q: Dict[str, Any] = {
        "question_type": qt, "question_text": text, "bloom_level": bloom, "difficulty": diff, "marks": marks,
        "explanation": _s(raw.get("explanation")) or None, "options": [], "match_pairs_json": None,
        "sequence_items_json": None, "assertion_text": None, "reason_text": None, "accepted_answers_json": None,
        "numeric_answer": None, "numeric_tolerance": None, "answer_key_text": None, "rubric_json": None,
        "voice_guidance": None, "source_refs_json": raw.get("source_chunk_ids") or raw.get("source_refs") or [],
        "generation_method": raw.get("generation_method", "llm"),
    }
    opts = raw.get("options") or []
    if qt in ("mcq", "assertion_reason", "true_false", "scenario", "application", "analytical") and opts:
        clean = []
        for o in opts:
            if isinstance(o, str):
                o = {"text": o, "correct": False}
            t = _s(o.get("text") or o.get("option_text"))
            if t:
                clean.append({"option_text": t, "is_correct": bool(o.get("is_correct", o.get("correct", False))),
                              "explanation": _s(o.get("explanation")) or None,
                              "misconception": _s(o.get("misconception")) or None})
        if not raw.get("options_have_flags", True):
            pass
        ci = raw.get("correct_index")
        if isinstance(ci, int) and 0 <= ci < len(clean) and not any(c["is_correct"] for c in clean):
            clean[ci]["is_correct"] = True
        q["options"] = clean

    if qt == "mcq":
        o = q["options"]
        if len(o) < 3 or sum(c["is_correct"] for c in o) != 1 or len({c["option_text"].lower() for c in o}) != len(o):
            return None
    elif qt == "true_false":
        ans = raw.get("answer", raw.get("correct"))
        if not q["options"]:
            if isinstance(ans, str):
                ans = ans.strip().lower() in ("true", "t", "yes")
            if ans is None:
                return None
            q["options"] = [{"option_text": "True", "is_correct": bool(ans), "explanation": None, "misconception": None},
                            {"option_text": "False", "is_correct": not bool(ans), "explanation": None, "misconception": None}]
        if len(q["options"]) != 2 or sum(c["is_correct"] for c in q["options"]) != 1:
            return None
    elif qt == "assertion_reason":
        a, r = _s(raw.get("assertion")), _s(raw.get("reason"))
        if not a or not r:
            return None
        q["assertion_text"], q["reason_text"] = a, r
        ci = raw.get("correct_index")
        if not q["options"]:
            if not isinstance(ci, int) or not 0 <= ci < 4:
                return None
            q["options"] = [{"option_text": t, "is_correct": i == ci, "explanation": None, "misconception": None}
                            for i, t in enumerate(ASSERTION_REASON_OPTIONS)]
        if len(q["options"]) != 4 or sum(c["is_correct"] for c in q["options"]) != 1:
            return None
    elif qt == "fill_blank":
        acc = [a for a in (raw.get("accepted_answers") or ([raw["answer"]] if raw.get("answer") else [])) if _s(a)]
        if not acc or "_" not in text:
            return None
        q["accepted_answers_json"] = [_s(a) for a in acc][:6]
    elif qt == "numerical":
        try:
            q["numeric_answer"] = float(raw.get("answer", raw.get("numeric_answer")))
        except (TypeError, ValueError):
            return None
        try:
            q["numeric_tolerance"] = float(raw["tolerance"]) if raw.get("tolerance") is not None else None
        except (TypeError, ValueError):
            q["numeric_tolerance"] = None
        q["answer_key_text"] = _s(raw.get("solution")) or None
    elif qt == "match_following":
        pairs = [{"left": _s(p.get("left")), "right": _s(p.get("right"))} for p in (raw.get("pairs") or []) if isinstance(p, dict)]
        pairs = [p for p in pairs if p["left"] and p["right"]]
        if len(pairs) < 3 or len({p["right"] for p in pairs}) != len(pairs):
            return None
        q["match_pairs_json"] = pairs[:8]
    elif qt == "sequence":
        items = [_s(i.get("text") if isinstance(i, dict) else i) for i in (raw.get("items") or [])]
        items = [i for i in items if i]
        if len(items) < 3 or len(set(items)) != len(items):
            return None
        q["sequence_items_json"] = [{"text": t} for t in items[:8]]
    elif qt in RUBRIC_TYPES or (qt in ("scenario", "application", "analytical") and not q["options"]):
        key = _s(raw.get("model_answer") or raw.get("answer_key"))
        rub = raw.get("rubric") or raw.get("key_points")
        points = []
        if isinstance(rub, dict):
            rub = rub.get("key_points")
        for i, p in enumerate(rub or []):
            if isinstance(p, str):
                p = {"text": p}
            t = _s(p.get("text"))
            if t:
                points.append({"id": f"p{i+1}", "text": t, "keywords": [k.lower() for k in (p.get("keywords") or extract_keywords(t))][:8],
                               "weight": float(p.get("weight", 1) or 1)})
        if not points and key:
            points = rubric_from_key(key)["key_points"]
        if len(points) < 2:
            return None
        mis = []
        for m in raw.get("misconceptions") or []:
            if isinstance(m, dict) and _s(m.get("text")):
                mis.append({"text": _s(m["text"]), "indicators": [_s(x).lower() for x in (m.get("indicators") or []) if _s(x)][:5],
                            "explanation": _s(m.get("explanation"))})
        q["rubric_json"] = {"key_points": points, "misconceptions": mis}
        q["answer_key_text"] = key or " ".join(p["text"] for p in points)
        if qt == "voice":
            q["voice_guidance"] = _s(raw.get("voice_guidance")) or "Speak clearly and explain in your own words."
    q["content_hash"] = content_hash(text)
    return q


# ====================================================================== LLM path
SCHEMA_DOC = """Each question is a JSON object with: "type", "question", "bloom_level" (remember|understand|apply|analyze|evaluate|create),
"difficulty" (easy|medium|hard), "marks", "explanation", "source_chunk_ids" (ids from the passages you used), plus:
- mcq: "options":[{"text","correct":true|false,"explanation","misconception"}] exactly 4 options, exactly one correct; "misconception" = the wrong idea a student has if they pick that distractor
- true_false: "answer": true|false
- fill_blank: the question contains "______"; "accepted_answers":[...]
- match_following: "pairs":[{"left","right"}] 3-5 pairs with unique rights
- sequence: "items":[...] 3-6 steps in the CORRECT order
- assertion_reason: "assertion","reason","correct_index" (0..3 mapping to the 4 standard options: 0 both true & R explains A, 1 both true but R not explanation, 2 A true R false, 3 A false R true)
- numerical: "answer": number, "tolerance": number, "solution": worked steps
- short_answer / analytical / scenario / application / voice: "model_answer", "rubric":[{"text","keywords":[...],"weight":1}] with 2-5 key points, optional "misconceptions":[{"text","indicators":[phrases],"explanation"}]
- scenario / application may also use "options" like mcq."""


def _llm_generate(context: str, topic: str, subject: str, wanted: List[Tuple[str, str]], difficulty: str,
                  extra: str = "") -> List[Dict[str, Any]]:
    spec = "\n".join(f"{i+1}. type={t}, bloom_level={b}" for i, (t, b) in enumerate(wanted))
    system = ("You are an expert university examiner. Create assessment questions ONLY from the supplied course passages. "
              "Never use outside facts. Questions must be unambiguous, answerable from the passages, and different from each "
              "other. Reply with JSON only: {\"questions\":[...]}.\n" + SCHEMA_DOC)
    user = (f"Subject: {subject}\nTopic: {topic}\nTarget difficulty: {difficulty}\n{extra}\n\nCourse passages:\n{context}\n\n"
            f"Create exactly {len(wanted)} questions with these types and Bloom levels:\n{spec}")
    data = llm_provider.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                  temperature=0.4, num_predict=4500)
    items = data.get("questions") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


# ====================================================================== extractive fallback
_DEF_RE = re.compile(r"^(?P<term>[A-Z][\w\-/() ]{2,60}?)\s+(?:is|are|refers to|means|can be defined as)\s+(?P<defn>.{20,220}?)[.]?$")
_SENT_RE = re.compile(r"(?<=[.!?।])\s+|\n+")


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_RE.split(text) if 35 <= len(s.strip()) <= 260]


def _pick_term(sentence: str, tf: Counter) -> Optional[str]:
    cands = [w for w in re.findall(r"[^\W\d_]{5,}", sentence) if w.lower() not in STOP]
    if not cands:
        return None
    cands.sort(key=lambda w: (-tf[w.lower()], -len(w)))
    return cands[0]


def _extractive(chunks: List[Dict[str, Any]], full_text: str, wanted: List[Tuple[str, str]], topic: str,
                rng: random.Random, voice_cfg: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    skipped: Dict[str, str] = {}
    text = full_text or "\n".join(c["content"] for c in chunks)
    sents = _sentences(text)
    cid_of = {}
    for c in chunks:
        for s in _sentences(c["content"]):
            cid_of.setdefault(s, c["chunk_id"])
    tf = Counter(w.lower() for w in re.findall(r"[^\W\d_]{5,}", text) if w.lower() not in STOP)
    terms = [w for w, _ in tf.most_common(40)]
    defs = []
    for s in sents:
        m = _DEF_RE.match(s)
        if m:
            defs.append((m.group("term").strip(), m.group("defn").strip(), s))
    rng.shuffle(sents)
    used: set = set()
    out: List[Dict[str, Any]] = []

    def _find_chunk(s):
        if s in cid_of:
            return cid_of[s]
        probe = s[:50]
        for c in chunks:
            if probe and probe in c["content"]:
                return c["chunk_id"]
        words = set(re.findall(r"\w{4,}", s.lower()))
        best = max(chunks, key=lambda c: len(words & set(re.findall(r"\w{4,}", c["content"].lower()))), default=None)
        return best["chunk_id"] if best else None

    def src(s):
        cid = _find_chunk(s)
        return [cid] if cid else []

    def next_sentence(pred=None):
        for s in sents:
            if s in used or (pred and not pred(s)):
                continue
            used.add(s)
            return s
        return None

    for qt, bloom in wanted:
        q: Optional[Dict[str, Any]] = None
        if qt == "fill_blank":
            s = next_sentence()
            term = _pick_term(s, tf) if s else None
            if s and term:
                q = {"type": "fill_blank", "question": "Fill in the blank: " + re.sub(re.escape(term), "______", s, count=1, flags=re.I),
                     "accepted_answers": [term], "explanation": f"Source: \"{s}\"", "source_chunk_ids": src(s)}
        elif qt == "mcq":
            s = next_sentence()
            term = _pick_term(s, tf) if s else None
            if s and term:
                pool = [t for t in terms if t.lower() != term.lower() and abs(len(t) - len(term)) <= 4]
                rng.shuffle(pool)
                if len(pool) >= 3:
                    opts = [{"text": term, "correct": True, "explanation": "Stated in the course material."}] + \
                           [{"text": d, "correct": False, "misconception": f"Confusing the required term with '{d}'."} for d in pool[:3]]
                    rng.shuffle(opts)
                    q = {"type": "mcq", "question": "Which term completes this statement from the course material? " +
                         re.sub(re.escape(term), "______", s, count=1, flags=re.I), "options": opts,
                         "explanation": f"Source: \"{s}\"", "source_chunk_ids": src(s)}
        elif qt == "true_false":
            s = next_sentence()
            term = _pick_term(s, tf) if s else None
            if s and term:
                if rng.random() < 0.5:
                    pool = [t for t in terms if t.lower() != term.lower()]
                    if pool:
                        q = {"type": "true_false", "question": "True or False: " + re.sub(re.escape(term), rng.choice(pool), s, count=1, flags=re.I),
                             "answer": False, "explanation": f"The material states: \"{s}\"", "source_chunk_ids": src(s)}
                if q is None:
                    q = {"type": "true_false", "question": "True or False: " + s, "answer": True,
                         "explanation": f"Stated in the material: \"{s}\"", "source_chunk_ids": src(s)}
        elif qt == "match_following":
            if len(defs) >= 3:
                pick = defs[:4]
                pairs = [{"left": t, "right": d[:110]} for t, d, _ in pick]
                if len({p["right"] for p in pairs}) == len(pairs):
                    q = {"type": "match_following", "question": "Match each term with its description from the course material.",
                         "pairs": pairs, "explanation": "Definitions are taken from the lecture material.",
                         "source_chunk_ids": [c for c in (_find_chunk(x[2]) for x in pick) if c]}
                    defs = defs[4:]
            else:
                skipped[qt] = "Not enough 'X is Y' definition sentences in the material."
        elif qt == "sequence":
            steps = re.findall(r"(?:^|\n|\s)(\d{1,2})[.)]\s+([^\n]{8,140}?)(?=\s+\d{1,2}[.)]\s|\n|$)", text)
            seq, expect = [], 1
            for n, body in steps:
                if int(n) == expect:
                    seq.append(body.strip().rstrip(";."))
                    expect += 1
            if len(seq) >= 3:
                q = {"type": "sequence", "question": "Arrange these steps in the correct order.", "items": seq[:6],
                     "explanation": "Order follows the numbered steps in the material.",
                     "source_chunk_ids": [c for c in dict.fromkeys(_find_chunk(x) for x in seq[:6]) if c]}
            else:
                skipped[qt] = "No numbered step list was found in the material."
        elif qt == "assertion_reason":
            s = next_sentence(lambda x: re.search(r"\b(because|since)\b", x, re.I) is not None)
            if s:
                a, r = re.split(r"\b(?:because|since)\b", s, maxsplit=1, flags=re.I)
                if len(a.strip()) > 15 and len(r.strip()) > 10:
                    q = {"type": "assertion_reason", "question": "Evaluate the assertion and the reason.",
                         "assertion": a.strip().rstrip(",;"), "reason": r.strip().rstrip("."), "correct_index": 0,
                         "explanation": f"The material states: \"{s}\"", "source_chunk_ids": src(s)}
            else:
                skipped[qt] = "No 'because/since' reasoning sentence in the material."
        elif qt in ("short_answer", "analytical", "scenario", "application", "voice"):
            if defs:
                term, defn, s = defs.pop(0)
                support = [x for x in sents if term.lower() in x.lower()][:3] or [s]
            else:
                s = next_sentence()
                if not s:
                    skipped[qt] = "Not enough material."
                    continue
                term, support = _pick_term(s, tf) or topic, [s]
            key = " ".join(support)
            stem = {"short_answer": f"In your own words, what is {term}?",
                    "analytical": f"Explain how {term} works and why it matters for {topic}.",
                    "scenario": f"A classmate is confused about {term}. Write what you would tell them to clear up the confusion.",
                    "application": f"Describe a situation where {term} applies and explain how it is used.",
                    "voice": f"Explain {term} in your own words as if teaching a friend."}[qt]
            pts = [{"text": x, "keywords": extract_keywords(x), "weight": 1} for x in support]
            if len(pts) < 2:
                pts.append({"text": f"Uses the term {term} correctly", "keywords": [term.lower()], "weight": 1})
            q = {"type": qt, "question": stem, "model_answer": key, "rubric": pts,
                 "explanation": f"Key idea: {key[:240]}", "source_chunk_ids": src(support[0])}
            if qt == "voice":
                q.update(voice_guidance="Take a moment to think, then speak in complete sentences.")
        elif qt == "numerical":
            skipped[qt] = "Numerical questions need the language model (it must compute and verify the answer)."
        if q:
            q["bloom_level"] = bloom
            q["generation_method"] = "extractive-template"
            v = validate_question({**q, "generation_method": "extractive-template"})
            if v:
                out.append(v)
        elif qt not in skipped:
            skipped[qt] = "Material was too short or unsuitable for this type."
    return out, skipped


# ====================================================================== public API
def plan_types(n: int, types: Optional[List[str]], bloom_focus: str, rng: random.Random) -> List[Tuple[str, str]]:
    types = [t for t in (types or QUESTION_TYPES) if t in QUESTION_TYPES] or list(QUESTION_TYPES)
    plan = []
    for i in range(n):
        t = types[i % len(types)]
        b = TYPE_BLOOM[t] if bloom_focus in ("mixed", "", None) else (bloom_focus if bloom_focus in BLOOM_LEVELS else TYPE_BLOOM[t])
        plan.append((t, b))
    return plan


def generate_questions(topic_title: str, subject_name: str, chunks: List[Dict[str, Any]], full_text: str, n: int,
                       types: Optional[List[str]], bloom_focus: str = "mixed", difficulty: str = "medium",
                       exclude_hashes: Optional[set] = None, voice_prep_sec: Optional[int] = None,
                       voice_max_time_sec: Optional[int] = None, seed: Optional[int] = None) -> Dict[str, Any]:
    if not chunks:
        raise ValueError("No approved, indexed material exists for this topic. Upload and index material first.")
    rng = random.Random(seed)
    exclude = set(exclude_hashes or [])
    context = "\n\n".join(f"[c{c['chunk_id']}] {c['content']}" for c in chunks)[:9000]
    wanted = plan_types(n, types, bloom_focus, rng)
    accepted: List[Dict[str, Any]] = []
    info: Dict[str, Any] = {"llm_used": False, "llm_error": None, "dropped_invalid": 0, "dropped_duplicate": 0, "skipped": {}}

    def add(q):
        if q["content_hash"] in exclude:
            info["dropped_duplicate"] += 1
            return False
        exclude.add(q["content_hash"])
        accepted.append(q)
        return True

    if llm_provider.available():
        remaining = list(wanted)
        for attempt in range(2):
            if not remaining:
                break
            try:
                raw = _llm_generate(context, topic_title, subject_name, remaining, difficulty)
                info["llm_used"] = True
            except (LLMError, LLMUnavailable) as exc:
                info["llm_error"] = str(exc)
                break
            produced = 0
            for r in raw:
                q = validate_question(r)
                if q is None:
                    info["dropped_invalid"] += 1
                    continue
                for j, (t, _b) in enumerate(remaining):
                    if t == q["question_type"]:
                        if add(q):
                            remaining.pop(j)
                            produced += 1
                        break
            if produced == 0:
                break
        wanted = remaining
    if wanted:
        fb, skipped = _extractive(chunks, full_text, wanted, topic_title, rng, {})
        info["skipped"] = skipped
        for q in fb:
            add(q)
    for i, q in enumerate(accepted):
        q["order_index"] = i
        q["difficulty"] = q["difficulty"] if q["difficulty"] in DIFFICULTIES else difficulty
        if q["generation_method"] == "extractive-template":
            q["difficulty"] = difficulty
        if q["question_type"] == "voice":
            q.update(voice_timing(q["marks"], voice_prep_sec, voice_max_time_sec))
    info["requested"], info["generated"] = n, len(accepted)
    return {"questions": accepted, "info": info}
