"""RAG, assessments, voice, integrity, gamification, adaptive, blockchain (required tests 8-15, 17)."""
import datetime
import io

from app.ai.llm_provider import llm_provider
from app.database import SessionLocal
from app.models.models import (
    AssessmentAttempt, AssessmentViolation, BlockchainRecord, Question, Student, StudentAnswer, TopicMastery,
    XPTransaction, VoiceAnswer, utcnow,
)
from app.speech import stt
from tests.conftest import NOTES, answer_all


def start(client, H, aid, who="s1@t.edu"):
    r = client.post(f"/api/student/assessments/{aid}/start", headers=H(who), json={})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ uploads / generation
def test_invalid_uploads_rejected(client, world, H):
    f = H("fac1@t.edu")
    form = {"subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"], "title": "Bad file"}
    assert client.post("/api/faculty/materials/upload", headers=f, data=form,
                       files={"file": ("x.exe", b"MZ....", "application/octet-stream")}).status_code == 415
    assert client.post("/api/faculty/materials/upload", headers=f, data=form,
                       files={"file": ("x.pdf", b"not a pdf at all", "application/pdf")}).status_code == 415
    assert client.post("/api/faculty/materials/upload", headers=f, data=form,
                       files={"file": ("x.docx", b"fake", "application/vnd.openxmlformats")}).status_code == 415
    assert client.post("/api/faculty/materials/upload", headers=f, data=form,
                       files={"file": ("e.txt", b"", "text/plain")}).status_code == 422
    # topic from another subject
    form2 = {**form, "topic_id": world["t2"]}
    assert client.post("/api/faculty/materials/upload", headers=f, data=form2,
                       files={"file": ("n.txt", b"some text here", "text/plain")}).status_code == 422


def test_generation_requires_material_and_produces_valid_questions(client, world, H):
    f = H("fac1@t.edu")
    body = {"subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"], "title": "Quiz", "num_questions": 4}
    assert client.post("/api/faculty/assessments/generate", headers=f, json=body).status_code == 409   # no material yet


def test_generation_extractive_and_lifecycle(client, world, H, material):
    f = H("fac1@t.edu")
    r = client.post("/api/faculty/assessments/generate", headers=f, json={
        "subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"], "title": "Quiz", "num_questions": 8,
        "types": ["mcq", "true_false", "fill_blank", "sequence", "match_following", "short_answer", "voice"]})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["status"] == "draft" and len(a["questions"]) >= 4
    assert a["generation_info"]["llm_used"] is False
    for q in a["questions"]:
        assert q["generation_method"] == "extractive-template" and q["source_refs"]
        if q["question_type"] == "mcq":
            assert sum(o["is_correct"] for o in q["options"]) == 1 and len(q["options"]) >= 3
    # students cannot see drafts
    assert client.get("/api/student/assessments", headers=H("s1@t.edu")).json() == []
    # edit + invalid edit
    q0 = a["questions"][0]
    bad = client.put(f"/api/faculty/assessments/{a['id']}/questions/{q0['id']}", headers=f,
                     json={"question_type": "mcq", "question_text": "Which?", "options": [{"option_text": "a", "is_correct": True},
                                                                                           {"option_text": "b", "is_correct": True}]})
    assert bad.status_code == 422
    # regenerate one
    rg = client.post(f"/api/faculty/assessments/{a['id']}/questions/{q0['id']}/regenerate", headers=f)
    assert rg.status_code in (200, 422)
    p = client.post(f"/api/faculty/assessments/{a['id']}/publish", headers=f, json={})
    assert p.status_code == 200 and p.json()["status"] == "published"
    # published assessments are immutable
    assert client.put(f"/api/faculty/assessments/{a['id']}/questions/{q0['id']}", headers=f,
                      json={"question_type": "true_false", "question_text": "Edit after publish?"}).status_code == 409
    assert client.get("/api/student/assessments", headers=H("s1@t.edu")).json()[0]["id"] == a["id"]


def test_malformed_llm_output_is_dropped_not_trusted(client, world, H, material):
    calls = {"n": 0}

    def bad_llm(messages, json_mode=False):
        calls["n"] += 1
        return '{"questions":[{"type":"mcq","question":"x"},{"type":"mcq","question":"Which organelle makes ATP?",' \
               '"options":[{"text":"A","correct":true},{"text":"B","correct":true}]}, "garbage", 5]}'
    llm_provider.set_override(bad_llm)
    r = client.post("/api/faculty/assessments/generate", headers=H("fac1@t.edu"), json={
        "subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"], "title": "Quiz", "num_questions": 3,
        "types": ["mcq", "true_false", "fill_blank"]})
    assert r.status_code == 201, r.text
    info = r.json()["generation_info"]
    assert info["llm_used"] and info["dropped_invalid"] >= 1          # invalid items were dropped
    assert all(q["generation_method"] == "extractive-template" for q in r.json()["questions"])
    llm_provider.set_override(lambda m, j=False: "this is not json at all")
    r = client.post("/api/faculty/assessments/generate", headers=H("fac1@t.edu"), json={
        "subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"], "title": "Quiz 2", "num_questions": 2,
        "types": ["mcq", "fill_blank"]})
    assert r.status_code == 201                                       # falls back safely


# ------------------------------------------------------------------ attempt flow, key hiding
def test_answer_keys_hidden_until_release_and_score_server_side(client, world, H, published):
    s = start(client, H, published["id"])
    blob = str(s)
    for k in ("is_correct", "answer_key", "accepted_answers", "rubric", "explanation", "misconception", "numeric_answer"):
        assert k not in blob, k
    att = s["attempt_id"]
    answer_all(client, H("s1@t.edu"), att, s["questions"])
    # client-supplied score fields are rejected
    assert client.post(f"/api/student/attempts/{att}/submit", headers=H("s1@t.edu"),
                       json={"answers": [], "score": 100}).status_code == 422
    r = client.post(f"/api/student/attempts/{att}/submit", headers=H("s1@t.edu"), json={"answers": []})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["answers_released"] is False and "questions" not in res          # no key / per-question truth yet
    assert 0 <= res["percentage"] <= 100
    # second submit is idempotent: no second score, no second XP
    xp1 = SessionLocal().query(XPTransaction).filter_by(student_id=world["s1"]).count()
    r2 = client.post(f"/api/student/attempts/{att}/submit", headers=H("s1@t.edu"), json={"answers": []})
    assert r2.status_code == 200 and r2.json()["percentage"] == res["percentage"]
    assert SessionLocal().query(XPTransaction).filter_by(student_id=world["s1"]).count() == xp1
    # release: faculty closes -> keys appear
    assert client.post(f"/api/faculty/assessments/{published['id']}/close", headers=H("fac1@t.edu")).status_code == 200
    full = client.get(f"/api/student/attempts/{att}/result", headers=H("s1@t.edu")).json()
    assert full["answers_released"] is True and len(full["questions"]) == len(s["questions"])
    # cannot start a closed assessment / re-attempt beyond max attempts
    assert client.post(f"/api/student/assessments/{published['id']}/start", headers=H("s2@t.edu"), json={}).status_code == 409


def test_max_attempts_and_deadline_enforced(client, world, H, published):
    s = start(client, H, published["id"])
    db = SessionLocal()
    at = db.query(AssessmentAttempt).get(s["attempt_id"])
    at.deadline_at = utcnow() - datetime.timedelta(minutes=5)       # simulate the time limit passing
    db.commit()
    db.close()
    q = s["questions"][0]
    r = client.put(f"/api/student/attempts/{s['attempt_id']}/answer", headers=H("s1@t.edu"),
                   json={"question_id": q["id"], "answer_text": "late"})
    assert r.status_code == 409                                       # late answers refused, attempt auto-submitted
    res = client.get(f"/api/student/attempts/{s['attempt_id']}/result", headers=H("s1@t.edu")).json()
    assert res["status"] == "auto_submitted"
    assert client.post(f"/api/student/assessments/{published['id']}/start", headers=H("s1@t.edu"), json={}).status_code == 409


# ------------------------------------------------------------------ voice & integrity
def test_voice_end_to_end_with_fake_transcriber(client, world, H, published):
    spoken = "Photosynthesis uses chlorophyll to absorb light and the Calvin cycle fixes carbon dioxide into glucose."
    stt.set_transcriber_for_tests(lambda path, lang: stt.Transcript(text=spoken, language="en", duration=12.0,
                                                                   segments=[], engine="fake"))
    s = start(client, H, published["id"])
    vq = next((q for q in s["questions"] if q["question_type"] == "voice"), None)
    assert vq and vq["voice_max_time_sec"] >= 15 and "voice_prep_sec" in vq
    r = client.post(f"/api/student/attempts/{s['attempt_id']}/voice/{vq['id']}", headers=H("s1@t.edu"),
                    data={"language": "English", "duration_seconds": "12"},
                    files={"audio": ("a.webm", b"\x1aE\xdf\xa3fake-audio", "audio/webm")})
    assert r.status_code == 200, r.text
    assert r.json()["transcript"] == spoken
    # too-long recording rejected before transcription
    r = client.post(f"/api/student/attempts/{s['attempt_id']}/voice/{vq['id']}", headers=H("s1@t.edu"),
                    data={"language": "English", "duration_seconds": "9999"},
                    files={"audio": ("a.webm", b"x", "audio/webm")})
    assert r.status_code == 422
    # voice for a non-voice question refused
    other = next(q for q in s["questions"] if q["question_type"] != "voice")
    assert client.post(f"/api/student/attempts/{s['attempt_id']}/voice/{other['id']}", headers=H("s1@t.edu"),
                       data={"language": "English"}, files={"audio": ("a.webm", b"x", "audio/webm")}).status_code == 422
    # another student cannot upload into this attempt
    assert client.post(f"/api/student/attempts/{s['attempt_id']}/voice/{vq['id']}", headers=H("s2@t.edu"),
                       data={"language": "English"}, files={"audio": ("a.webm", b"x", "audio/webm")}).status_code == 404
    res = client.post(f"/api/student/attempts/{s['attempt_id']}/submit", headers=H("s1@t.edu"), json={"answers": []}).json()
    db = SessionLocal()
    va = db.query(VoiceAnswer).one()
    assert va.transcript == spoken and va.audio_file_path is None          # audio deleted after transcription
    ans = db.query(StudentAnswer).filter_by(question_id=vq["id"]).one()
    assert ans.score_awarded > 0 and ans.evaluation_method                  # graded on content
    raw = db.execute(__import__("sqlalchemy").text("select transcript from voice_answers")).scalar()
    assert spoken not in raw                                                # encrypted at rest
    db.close()
    # faculty sees transcript & can review
    det = client.get(f"/api/faculty/attempts/{s['attempt_id']}", headers=H("fac1@t.edu")).json()
    assert any(a["voice"] and a["voice"]["transcript"] == spoken for a in det["answers"])


def test_voice_unavailable_gives_typed_fallback(client, world, H, published):
    stt.set_transcriber_for_tests(lambda p, l: (_ for _ in ()).throw(stt.SpeechUnavailable("Whisper model not installed")))
    s = start(client, H, published["id"])
    vq = next(q for q in s["questions"] if q["question_type"] == "voice")
    r = client.post(f"/api/student/attempts/{s['attempt_id']}/voice/{vq['id']}", headers=H("s1@t.edu"),
                    data={"language": "English"}, files={"audio": ("a.webm", b"x", "audio/webm")})
    assert r.status_code == 503 and "type your answer" in r.json()["detail"]
    ok = client.put(f"/api/student/attempts/{s['attempt_id']}/answer", headers=H("s1@t.edu"),
                    json={"question_id": vq["id"], "answer_text": "chlorophyll absorbs light", "input_mode": "typed"})
    assert ok.status_code == 200


def test_violations_recorded_never_punish_automatically(client, world, H, published):
    s = start(client, H, published["id"])
    for t in ("tab_hidden", "window_blur", "fullscreen_exit", "copy_paste"):
        r = client.post(f"/api/student/attempts/{s['attempt_id']}/violations", headers=H("s1@t.edu"),
                        json={"violation_type": t})
        assert r.status_code == 200 and r.json()["action"] in ("log", "warn")
    assert client.post(f"/api/student/attempts/{s['attempt_id']}/violations", headers=H("s1@t.edu"),
                       json={"violation_type": "face_not_detected"}).status_code == 422
    assert client.post(f"/api/student/attempts/{s['attempt_id']}/violations", headers=H("s2@t.edu"),
                       json={"violation_type": "tab_hidden"}).status_code == 404
    client.post(f"/api/student/attempts/{s['attempt_id']}/submit", headers=H("s1@t.edu"), json={"answers": []})
    det = client.get(f"/api/faculty/attempts/{s['attempt_id']}", headers=H("fac1@t.edu")).json()
    assert len(det["violations"]) == 4 and det["status"] == "completed"       # still a normal completed attempt
    v = det["violations"][0]
    r = client.post(f"/api/faculty/violations/{v['id']}/review", headers=H("fac1@t.edu"),
                    json={"review_status": "dismissed", "note": "network glitch"})
    assert r.status_code == 200
    assert client.post(f"/api/faculty/violations/{v['id']}/review", headers=H("fac2@t.edu"),
                       json={"review_status": "confirmed"}).status_code == 403


# ------------------------------------------------------------------ gamification / adaptive / dashboard
def test_xp_idempotent_and_streak_rules(client, world):
    from app.gamification import engine as g
    db = SessionLocal()
    st = db.query(Student).get(world["s1"])
    assert g.award_xp(db, st, 50, "assessment", "attempt-1", "x")["awarded"] == 50
    assert g.award_xp(db, st, 50, "assessment", "attempt-1", "x")["duplicate"] is True
    assert st.total_xp == 50
    today = g.student_today(st)
    st.streak_last_date, st.streak_days = today, 3
    assert g.update_streak(db, st)["current_streak"] == 3                      # same day: unchanged
    st.streak_last_date = today - datetime.timedelta(days=1)
    assert g.update_streak(db, st)["current_streak"] == 4                      # next day: +1
    st.streak_last_date = today - datetime.timedelta(days=3)
    assert g.update_streak(db, st)["current_streak"] == 1                      # gap: reset
    st.streak_last_date, st.streak_days = today - datetime.timedelta(days=5), 9
    assert g.effective_streak(st) == 0                                         # lapsed streak not displayed
    db.close()


def test_leaderboard_opt_in_and_privacy(client, world, H):
    h1, h2 = H("s1@t.edu"), H("s2@t.edu")
    assert client.patch("/api/student/privacy", headers=h1, json={"leaderboard_opt_in": True}).status_code == 422   # alias needed
    assert client.patch("/api/student/privacy", headers=h1, json={"leaderboard_opt_in": True, "display_alias": "Fox_1"}).status_code == 200
    lb = client.get("/api/student/leaderboard", headers=h2).json()
    assert [e["alias"] for e in lb["entries"]] == ["Fox_1"] and "Student 1" not in str(lb) and "R001" not in str(lb)
    client.patch("/api/student/privacy", headers=h1, json={"leaderboard_opt_in": False})
    assert client.get("/api/student/leaderboard", headers=h2).json()["entries"] == []


def test_badge_share_requires_confirmation(client, world, H, published):
    s = start(client, H, published["id"])
    answer_all(client, H("s1@t.edu"), s["attempt_id"], s["questions"])
    client.post(f"/api/student/attempts/{s['attempt_id']}/submit", headers=H("s1@t.edu"), json={"answers": []})
    earned = [b for b in client.get("/api/student/badges", headers=H("s1@t.edu")).json() if b["earned"]]
    assert earned, "first completed assessment should unlock a badge"
    eid = earned[0]["earned_id"]
    assert client.post(f"/api/student/badges/{eid}/share", headers=H("s1@t.edu"), json={"confirm": False}).status_code == 422
    assert client.post(f"/api/student/badges/{eid}/share", headers=H("s2@t.edu"), json={"confirm": True}).status_code == 404
    tok = client.post(f"/api/student/badges/{eid}/share", headers=H("s1@t.edu"), json={"confirm": True}).json()["token"]
    pub = client.get(f"/api/public/badge/{tok}").json()
    assert pub["holder"] == "A student" and "Student 1" not in str(pub)
    client.delete(f"/api/student/badge-shares/{tok}", headers=H("s1@t.edu"))
    assert client.get(f"/api/public/badge/{tok}").status_code == 404


def test_dashboards_match_database(client, world, H, published):
    s = start(client, H, published["id"])
    answer_all(client, H("s1@t.edu"), s["attempt_id"], s["questions"])
    res = client.post(f"/api/student/attempts/{s['attempt_id']}/submit", headers=H("s1@t.edu"), json={"answers": []}).json()
    dash = client.get("/api/student/dashboard", headers=H("s1@t.edu")).json()
    db = SessionLocal()
    m = db.query(TopicMastery).filter_by(student_id=world["s1"]).one()
    st = db.query(Student).get(world["s1"])
    assert dash["gamification"]["xp"] == st.total_xp > 0
    assert dash["mastery"]["topics"][0]["mastery_score"] == m.mastery_score
    assert abs(m.mastery_score - res["percentage"]) < 0.5                      # first attempt: mastery == score
    fa = client.get(f"/api/faculty/analytics?subject_id={world['bio']}", headers=H("fac1@t.edu")).json()
    assert fa["attempts"] == 1 and fa["avg_percentage"] == res["percentage"] and fa["students"] == 3
    assert {r["roll_number"] for r in fa["roster"]} == {"R001", "R002", "R003"}   # primary handler: all classes
    ad = client.get("/api/advisor/dashboard", headers=H("adv1@t.edu")).json()
    row = next(r for r in ad["students"] if r["student_id"] == world["s1"])
    assert row["avg_mastery"] == m.mastery_score and row["attempts"] == 1
    # new student with no data: honest empty state
    empty = client.get("/api/student/dashboard", headers=H("s3@t.edu")).json()
    assert empty["empty"] is True and empty["mastery"]["topics"] == []
    db.close()


def test_override_updates_grade_version_and_ledger(client, world, H, published, eth):
    s = start(client, H, published["id"])
    answer_all(client, H("s1@t.edu"), s["attempt_id"], s["questions"])
    client.post(f"/api/student/attempts/{s['attempt_id']}/submit", headers=H("s1@t.edu"), json={"answers": []})
    det = client.get(f"/api/faculty/attempts/{s['attempt_id']}", headers=H("fac1@t.edu")).json()
    row = det["answers"][0]
    r = client.post(f"/api/faculty/answers/{row['answer_id']}/grade", headers=H("fac1@t.edu"),
                    json={"score": row["question"]["marks"], "comment": "ok"})
    assert r.status_code == 200 and r.json()["grade_version"] == 2
    assert client.post(f"/api/faculty/answers/{row['answer_id']}/grade", headers=H("fac1@t.edu"),
                       json={"score": 9999}).status_code == 422
    assert client.post(f"/api/faculty/answers/{row['answer_id']}/grade", headers=H("fac2@t.edu"),
                       json={"score": 1}).status_code == 403
    db = SessionLocal()
    recs = db.query(BlockchainRecord).order_by(BlockchainRecord.version).all()
    assert [x.status for x in recs] == ["superseded", "anchored"]
    db.close()


# ------------------------------------------------------------------ RAG
def test_rag_permission_filtered_retrieval_and_citations(client, world, H, material):
    q = {"message": "What does chlorophyll do in photosynthesis?", "language": "English", "subject_id": world["bio"]}
    r = client.post("/api/rag/companion", headers=H("s1@t.edu"), json=q)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["is_rag_grounded"] and d["sources"] and d["sources"][0]["title"] == "Notes"
    assert d["answer_type"] in ("extractive", "grounded")
    # other-class student of same subject: material is subject-wide so visible; chemistry student cannot
    assert client.post("/api/rag/companion", headers=H("s3@t.edu"), json={**q, "subject_id": world["che"]}).json()["sources"] == []
    # class-restricted material is invisible to other classes
    f = H("fac1@t.edu")
    up = client.post("/api/faculty/materials/upload", headers=f, data={
        "subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"], "title": "ClassA only",
        "class_id": world["ca"]}, files={"file": ("b.txt", b"Quantum zebra manifesto describes zebra quantum tunnelling rules.", "text/plain")})
    assert up.status_code == 201
    qq = {"message": "quantum zebra tunnelling", "language": "English", "subject_id": world["bio"]}
    a = client.post("/api/rag/companion", headers=H("s1@t.edu"), json=qq).json()
    b = client.post("/api/rag/companion", headers=H("s3@t.edu"), json=qq).json()
    assert any(s["title"] == "ClassA only" for s in a["sources"])
    assert not any(s["title"] == "ClassA only" for s in b["sources"])
    # unapproved material is hidden from students
    mid = up.json()["id"]
    client.patch(f"/api/faculty/materials/{mid}", headers=f, json={"is_approved": False})
    a2 = client.post("/api/rag/companion", headers=H("s1@t.edu"), json=qq).json()
    assert not any(s["title"] == "ClassA only" for s in a2["sources"])
    # subject the student is not enrolled in
    assert client.post("/api/rag/companion", headers=H("s1@t.edu"), json={**q, "subject_id": 9999}).status_code == 403


def test_rag_refuses_when_no_material_and_hides_nothing_unrelated(client, world, H, material):
    r = client.post("/api/rag/companion", headers=H("s1@t.edu"),
                    json={"message": "Explain the French revolution causes", "language": "English", "subject_id": world["bio"]})
    d = r.json()
    assert d["answer_type"] == "insufficient_material" and d["is_rag_grounded"] is False and d["sources"] == []


def test_conversation_privacy_clear_and_zero_retention(client, world, H, material):
    h = H("s1@t.edu")
    q = {"message": "What is chlorophyll?", "language": "English", "subject_id": world["bio"]}
    client.post("/api/rag/companion", headers=h, json=q)
    assert len(client.get("/api/rag/conversations", headers=h).json()) == 1
    assert client.get("/api/rag/conversations", headers=H("s2@t.edu")).json() == []
    assert client.delete("/api/rag/conversations", headers=h).json()["deleted"] == 1
    client.patch("/api/student/privacy", headers=h, json={"conversation_retention_days": 0})
    assert client.post("/api/rag/companion", headers=h, json=q).json()["stored"] is False
    assert client.get("/api/rag/conversations", headers=h).json() == []


# ------------------------------------------------------------------ blockchain
def _complete(client, H, aid):
    s = start(client, H, aid)
    answer_all(client, H("s1@t.edu"), s["attempt_id"], s["questions"])
    client.post(f"/api/student/attempts/{s['attempt_id']}/submit", headers=H("s1@t.edu"), json={"answers": []})
    return s["attempt_id"]


def test_blockchain_anchor_verify_and_tamper_detection(client, world, H, published, eth):
    att = _complete(client, H, published["id"])
    recs = client.get("/api/blockchain/records", headers=H("s1@t.edu")).json()
    assert len(recs) == 1 and recs[0]["status"] == "anchored" and recs[0]["transaction_hash"].startswith("0x")
    ref = recs[0]["record_ref"]
    assert "R001" not in str(recs) and "s1@t.edu" not in str(recs)               # nothing personal in the ledger view
    ok = client.post("/api/blockchain/verify", headers=H("s1@t.edu"), json={"record_ref": ref}).json()
    assert ok["result"] == "VERIFIED"
    # on-chain data is just an opaque id + a 32-byte keyed hash (no marks / names)
    w3, contract = eth
    exists, matches, revoked, ts, reg = contract.functions.verifyRecord(ref, bytes.fromhex(recs[0]["hash"])).call()
    assert exists and matches and not revoked
    # tamper with the database -> mismatch
    db = SessionLocal()
    a = db.query(AssessmentAttempt).get(att)
    a.total_score = a.total_score + 7
    db.commit()
    db.close()
    bad = client.post("/api/blockchain/verify", headers=H("s1@t.edu"), json={"record_ref": ref}).json()
    assert bad["result"] == "MISMATCH" and bad["database_matches_stored_hash"] is False
    hist = client.get(f"/api/blockchain/records/{ref}/history", headers=H("s1@t.edu")).json()
    assert [h["result"] for h in hist] == ["MISMATCH", "VERIFIED"]
    # nobody else can verify / list this record
    assert client.post("/api/blockchain/verify", headers=H("s2@t.edu"), json={"record_ref": ref}).status_code == 404
    assert client.post("/api/blockchain/verify", headers=H("mgmt@t.edu"), json={"record_ref": ref}).status_code == 403
    assert client.post("/api/blockchain/verify", headers=H("fac2@t.edu"), json={"record_ref": ref}).status_code == 404
    assert client.post("/api/blockchain/verify", headers=H("fac1@t.edu"), json={"record_ref": ref}).status_code == 200


def test_blockchain_unavailable_is_pending_then_retry(client, world, H, published):
    att = _complete(client, H, published["id"])                                  # no chain configured
    rec = client.get("/api/blockchain/records", headers=H("s1@t.edu")).json()[0]
    assert rec["status"] == "pending" and rec["error"]                           # honest: NOT anchored
    v = client.post("/api/blockchain/verify", headers=H("s1@t.edu"), json={"record_ref": rec["record_ref"]}).json()
    assert v["result"] == "NOT_ANCHORED"
    from web3 import EthereumTesterProvider, Web3
    from app.blockchain import service as chain
    w3 = Web3(EthereumTesterProvider())
    art = chain.load_artifact()
    h = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"]).constructor().transact({"from": w3.eth.accounts[0]})
    c = w3.eth.contract(address=w3.eth.wait_for_transaction_receipt(h).contractAddress, abi=art["abi"])
    chain.set_test_chain(w3, c, None)
    assert client.post("/api/blockchain/retry-pending", headers=H("s1@t.edu")).status_code == 403
    assert client.post("/api/blockchain/retry-pending", headers=H("admin@t.edu")).json()["anchored"] == 1
    assert client.post("/api/blockchain/verify", headers=H("s1@t.edu"), json={"record_ref": rec["record_ref"]}).json()["result"] == "VERIFIED"
    chain.reset()
