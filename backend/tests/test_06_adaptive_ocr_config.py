"""Adaptive learning on known score scenarios, OCR with the real Tesseract install, startup-configuration validation."""
import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.adaptive import recommendations as rec
from app.adaptive.mastery_engine import adaptive_mastery_engine as eng
from app.database import SessionLocal
from app.models.models import (
    Assessment, AssessmentAttempt, LearningMaterial, Recommendation, RevisionQueue, Student, Topic, TopicMastery, utcnow,
)


# ------------------------------------------------------------------ helpers
def _assessment(db, world, topic_id, kind="daily"):
    a = Assessment(subject_id=world["bio"], unit_id=world["u1"], topic_id=topic_id, faculty_id=world["f1"],
                   title=f"A{topic_id}-{kind}", kind=kind, status="published", is_published=True)
    db.add(a)
    db.flush()
    return a


def _attempt(db, world, student_key, topic_id, pct, kind="daily"):
    a = _assessment(db, world, topic_id, kind)
    att = AssessmentAttempt(assessment_id=a.id, student_id=world[student_key], status="completed", completed_at=utcnow(),
                            total_score=pct, max_score=100.0, percentage=float(pct))
    db.add(att)
    db.commit()
    out = eng.update_after_attempt(db, att)
    rec.regenerate(db, db.get(Student, world[student_key]))
    return out


def _recs(db, world, key):
    return db.query(Recommendation).filter_by(student_id=world[key]).all()


@pytest.fixture()
def db(world):
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def topic2(db, world):
    t = Topic(unit_id=world["u1"], title="Respiration", order_index=2)
    db.add(t)
    db.commit()
    return t.id


# ------------------------------------------------------------------ adaptive scenarios
def test_no_data_is_not_a_poor_score(db, world, topic2):
    prof = eng.profile(db, world["s1"])
    assert prof["topics"] == [] and prof["weaknesses"] == []
    plan = rec.regenerate(db, db.get(Student, world["s1"]))
    assert _recs(db, world, "s1") == [] and plan["revision_items"] == 0
    assert "first assessment" in plan["summary"]
    # attempting ONE topic must not turn the untouched topic into a weakness
    _attempt(db, world, "s1", world["t1"], 90)
    prof = eng.profile(db, world["s1"])
    assert [t["topic_id"] for t in prof["topics"]] == [world["t1"]]
    assert all(r.topic_id != topic2 for r in _recs(db, world, "s1"))


def test_weak_topic_gets_explained_recommendation_and_easy_practice(db, world, material):
    out = _attempt(db, world, "s1", world["t1"], 40)
    assert out["new_mastery"] == 40.0 and out["next_difficulty"] == "easy" and out["trend"] == "insufficient_data"
    (r,) = _recs(db, world, "s1")
    assert r.title == "Revise Photosynthesis" and r.priority == "high" and "mastery is 40%" in r.reason
    assert "Notes" in r.description and "easy-difficulty" in r.description     # a real, visible material from the DB
    assert db.query(RevisionQueue).filter_by(student_id=world["s1"]).count() == 1


def test_strong_topic_is_mastered_and_challenged(db, world):
    for p in (85, 90, 95):
        out = _attempt(db, world, "s1", world["t1"], p)
    assert out["new_mastery"] >= 80 and out["completion_status"] == "mastered" and out["next_difficulty"] == "hard"
    titles = [r.title for r in _recs(db, world, "s1")]
    assert titles == ["Challenge yourself: Photosynthesis"]


def test_recommendations_update_when_new_results_arrive(db, world):
    _attempt(db, world, "s1", world["t1"], 40)
    assert len(_recs(db, world, "s1")) == 1
    _attempt(db, world, "s1", world["t1"], 92)
    m = db.query(TopicMastery).filter_by(student_id=world["s1"], topic_id=world["t1"]).first()
    assert m.mastery_score > 40 and m.trend == "improving" and m.attempts_count == 2
    _attempt(db, world, "s1", world["t1"], 92)
    m = db.query(TopicMastery).filter_by(student_id=world["s1"], topic_id=world["t1"]).first()
    assert m.mastery_score >= 60
    assert not [r for r in _recs(db, world, "s1") if r.title.startswith("Revise")]       # no longer flagged weak


def test_declining_trend_is_detected_and_explained(db, world):
    for p in (90, 90, 90, 50, 50, 50):
        _attempt(db, world, "s1", world["t1"], p)
    m = db.query(TopicMastery).filter_by(student_id=world["s1"], topic_id=world["t1"]).first()
    assert m.trend == "declining"
    assert m.current_difficulty != "hard"


def test_practice_counts_half_so_banks_cannot_inflate_mastery(db, world):
    _attempt(db, world, "s1", world["t1"], 40)
    after_practice = _attempt(db, world, "s1", world["t1"], 100, kind="practice")["new_mastery"]
    _attempt(db, world, "s2", world["t1"], 40)
    after_exam = _attempt(db, world, "s2", world["t1"], 100)["new_mastery"]
    assert 40 < after_practice < after_exam


def test_students_are_independent_and_materials_respect_visibility(db, world, material, client, H):
    _attempt(db, world, "s1", world["t1"], 30)
    assert _recs(db, world, "s2") == []
    # hide the only material from students -> the recommendation must not name it
    db.query(LearningMaterial).update({"is_approved": False})
    db.commit()
    rec.regenerate(db, db.get(Student, world["s1"]))
    (r,) = _recs(db, world, "s1")
    assert "Notes" not in r.description and "class notes" in r.description
    dash = client.get("/api/student/dashboard", headers=H("s1@t.edu")).json()
    assert dash["recommendations"][0]["reason"] == r.reason                  # dashboard shows the stored DB record


def test_end_to_end_attempt_updates_mastery_and_dashboard(client, world, H, published, db):
    from conftest import answer_all
    from test_02_academic import start
    s = start(client, H, published["id"])
    answer_all(client, H("s1@t.edu"), s["attempt_id"], s["questions"])
    client.post(f"/api/student/attempts/{s['attempt_id']}/submit", headers=H("s1@t.edu"), json={"answers": []})
    dash = client.get("/api/student/dashboard", headers=H("s1@t.edu")).json()
    m = db.query(TopicMastery).filter_by(student_id=world["s1"]).one()
    att = db.query(AssessmentAttempt).get(s["attempt_id"])
    assert m.mastery_score == round(att.percentage, 1)
    shown = [t for t in dash["mastery"]["topics"]] if "mastery" in dash else []
    assert not shown or shown[0]["mastery_score"] == m.mastery_score


# ------------------------------------------------------------------ OCR (real Tesseract, English only)
def _png(text, size=(900, 220)):
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except OSError:
        f = ImageFont.load_default(size=40)
    d.text((20, 70), text, fill="black", font=f)
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def _upload(client, H, world, name, data, ctype):
    return client.post("/api/faculty/materials/upload", headers=H("fac1@t.edu"), data={
        "subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"], "title": name},
        files={"file": (name, data, ctype)})


def test_ocr_extracts_text_from_an_image_and_makes_it_searchable(client, world, H):
    from app.rag.document_parser import ocr_available
    if not ocr_available():
        pytest.skip("Tesseract is not installed in this environment")
    r = _upload(client, H, world, "scan.png", _png("Mitochondria produce ATP through respiration"), "image/png")
    assert r.status_code == 201 and r.json()["status"] == "indexed", r.text
    d = client.post("/api/rag/companion", headers=H("s1@t.edu"), json={
        "message": "Which organelle produces ATP through respiration?", "language": "English", "subject_id": world["bio"]}).json()
    assert d["sources"] and "mitochondria" in d["sources"][0]["snippet"].lower()


def test_ocr_blank_image_and_corrupt_image_fail_cleanly(client, world, H):
    from app.rag.document_parser import ocr_available
    if not ocr_available():
        pytest.skip("Tesseract is not installed in this environment")
    blank = _upload(client, H, world, "blank.png", _png(""), "image/png")
    assert blank.status_code == 201 and blank.json()["status"] == "error"          # nothing to index -> honest error
    bad = _upload(client, H, world, "bad.png", b"\x89PNG not really", "image/png")
    assert bad.status_code in (201, 400, 415, 422) and bad.status_code != 500
    if bad.status_code == 201:
        assert bad.json()["status"] == "error"


def test_ocr_missing_language_pack_is_reported_not_crashed(client, world, H, monkeypatch):
    from app.config import settings
    from app.rag.document_parser import ocr_available
    if not ocr_available():
        pytest.skip("Tesseract is not installed in this environment")
    monkeypatch.setattr(settings, "OCR_LANGS", "eng+tam+xyz")                       # packs that are not installed
    r = _upload(client, H, world, "scan.png", _png("Photosynthesis in leaves"), "image/png")
    assert r.status_code == 201 and r.json()["status"] == "indexed"                 # English text is still read ...
    body = str(r.json())
    assert "not installed: tam, xyz" in body                                        # ... but the gap is stated, not hidden
    cap = client.get("/api/rag/capabilities", headers=H("s1@t.edu")).json()["ocr_languages"]
    assert cap["missing"] == ["tam", "xyz"] and cap["usable"] == ["eng"]
    monkeypatch.setattr(settings, "OCR_LANGS", "tam")                                # nothing usable -> clear failure
    r2 = _upload(client, H, world, "scan2.png", _png("Photosynthesis in leaves"), "image/png")
    assert r2.json()["status"] == "error"


def test_disallowed_upload_types_are_rejected(client, world, H):
    assert _upload(client, H, world, "x.exe", b"MZ", "application/octet-stream").status_code == 415
    assert _upload(client, H, world, "x.svg", b"<svg onload=alert(1)>", "image/svg+xml").status_code == 415


# ------------------------------------------------------------------ startup configuration
def _settings(**kw):
    from app.config import Settings
    return Settings(_env_file=None, **kw)


GOOD_PROD = dict(ENVIRONMENT="production", AUTH_MODE="firebase", FIREBASE_PROJECT_ID="p", FIREBASE_WEB_API_KEY="k",
                 DATA_ENCRYPTION_KEY="p4X9xjWlewKX7Rw1Me-xYux7HWmjq5gEiyqF8UNgik0=", COOKIE_SECURE=True,
                 CORS_ORIGINS="https://school.example", BLOCKCHAIN_ENABLED=False)


def test_production_requires_secure_settings():
    _settings(**GOOD_PROD).validate_for_startup()                                   # a complete config starts
    for key, bad in (("AUTH_MODE", "local"), ("FIREBASE_PROJECT_ID", ""), ("DATA_ENCRYPTION_KEY", ""),
                     ("COOKIE_SECURE", False), ("CORS_ORIGINS", "*"), ("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")):
        with pytest.raises(RuntimeError):
            _settings(**{**GOOD_PROD, key: bad}).validate_for_startup()


def test_invalid_keys_and_addresses_fail_fast_with_clear_messages():
    with pytest.raises(RuntimeError, match="DATA_ENCRYPTION_KEY"):
        _settings(DATA_ENCRYPTION_KEY="not-a-fernet-key").validate_for_startup()
    with pytest.raises(RuntimeError, match="BLOCKCHAIN_HMAC_KEY"):
        _settings(BLOCKCHAIN_HMAC_KEY="short").validate_for_startup()
    with pytest.raises(RuntimeError, match="CONTRACT_ADDRESS"):
        _settings(CONTRACT_ADDRESS="0x123").validate_for_startup()
    with pytest.raises(RuntimeError, match="BLOCKCHAIN_RPC_URL"):
        _settings(BLOCKCHAIN_RPC_URL="localhost:8545").validate_for_startup()
    with pytest.raises(RuntimeError, match="BLOCKCHAIN_PRIVATE_KEY"):
        _settings(BLOCKCHAIN_PRIVATE_KEY="zz").validate_for_startup()


def test_hardhat_public_key_is_refused_outside_local_development():
    hardhat0 = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    _settings(BLOCKCHAIN_PRIVATE_KEY=hardhat0).validate_for_startup()                # local dev: allowed
    with pytest.raises(RuntimeError, match="publicly known"):
        _settings(**{**GOOD_PROD, "BLOCKCHAIN_ENABLED": True, "BLOCKCHAIN_PRIVATE_KEY": hardhat0}).validate_for_startup()
    with pytest.raises(RuntimeError, match="localhost|local node"):
        _settings(**{**GOOD_PROD, "BLOCKCHAIN_ENABLED": True,
                     "BLOCKCHAIN_RPC_URL": "http://127.0.0.1:8545"}).validate_for_startup()
