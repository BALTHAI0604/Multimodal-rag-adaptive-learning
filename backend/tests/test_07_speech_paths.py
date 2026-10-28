"""Speech paths that do not need a model: validation, honest unavailability, temp-file cleanup.
(Real Whisper/Piper recognition is NOT covered here - it needs model downloads; see README 'Not verified'.)"""
from pathlib import Path

import pytest

from app.config import settings
from app.speech import stt, tts
from test_02_academic import start


def _tmp_files():
    return [p for p in (settings.UPLOAD_DIR).rglob("*") if p.is_file() and p.parent.name in ("tmp", "voice")]


def post_audio(client, H, name="a.webm", data=b"\x1aE\xdf\xa3audio", who="s1@t.edu"):
    return client.post("/api/rag/transcribe", headers=H(who), data={"language": "English"},
                       files={"audio": (name, data, "application/octet-stream")})


def test_transcribe_rejects_unsupported_types_and_oversize(client, world, H, monkeypatch):
    assert post_audio(client, H, "a.exe").status_code == 415
    monkeypatch.setattr(settings, "MAX_AUDIO_ANSWER_MB", 0)
    assert post_audio(client, H, data=b"x" * 2048).status_code == 413
    assert _tmp_files() == []
    assert client.post("/api/rag/transcribe", data={"language": "English"},
                       files={"audio": ("a.webm", b"x", "audio/webm")}).status_code in (401, 403)


def test_transcribe_without_whisper_is_a_clear_503_and_leaves_no_file(client, world, H):
    stt.set_transcriber_for_tests(None)
    if stt.is_available():
        pytest.skip("faster-whisper is installed here; this test covers the not-installed path")
    r = post_audio(client, H)
    assert r.status_code == 503 and "faster-whisper" in r.json()["detail"] and "type your question" in r.json()["detail"]
    assert _tmp_files() == []
    assert client.get("/api/rag/capabilities", headers=H("s1@t.edu")).json()["speech_to_text"] is False


def test_transcribe_cleans_up_even_when_the_engine_crashes(client, world, H):
    stt.set_transcriber_for_tests(lambda p, l: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ValueError):
        post_audio(client, H)                         # TestClient re-raises unhandled server errors
    assert _tmp_files() == []


def test_voice_answer_cleans_up_when_the_engine_crashes(client, world, H, published):
    s = start(client, H, published["id"])
    vq = next(q for q in s["questions"] if q["question_type"] == "voice")
    stt.set_transcriber_for_tests(lambda p, l: (_ for _ in ()).throw(ValueError("boom")))
    with pytest.raises(ValueError):
        client.post(f"/api/student/attempts/{s['attempt_id']}/voice/{vq['id']}", headers=H("s1@t.edu"),
                    data={"language": "English"}, files={"audio": ("a.webm", b"x", "audio/webm")})
    assert _tmp_files() == []


def test_empty_transcript_is_rejected_not_stored(client, world, H, published):
    s = start(client, H, published["id"])
    vq = next(q for q in s["questions"] if q["question_type"] == "voice")
    stt.set_transcriber_for_tests(lambda p, l: stt.Transcript(text="  ", language="en", duration=1.0, segments=[], engine="fake"))
    r = client.post(f"/api/student/attempts/{s['attempt_id']}/voice/{vq['id']}", headers=H("s1@t.edu"),
                    data={"language": "English"}, files={"audio": ("a.webm", b"x", "audio/webm")})
    assert r.status_code == 422 and _tmp_files() == []


def test_tts_reports_unavailable_voices_honestly(client, world, H):
    cap = client.get("/api/rag/capabilities", headers=H("s1@t.edu")).json()
    assert cap["tts"] == {l: False for l in ("English", "Hindi", "Tamil", "Kannada", "Malayalam")}
    r = client.post("/api/rag/tts", headers=H("s1@t.edu"), json={"text": "hello", "language": "Tamil"})
    assert r.status_code == 503 and "No server voice is configured for Tamil" in r.json()["detail"]
    assert client.post("/api/rag/tts", headers=H("s1@t.edu"), json={"text": "hello", "language": "French"}).status_code == 422


def test_tts_voice_is_only_reported_when_binary_and_file_exist(tmp_path, monkeypatch):
    voice = tmp_path / "en.onnx"
    voice.write_bytes(b"x")
    monkeypatch.setattr(settings, "PIPER_VOICES_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "PIPER_VOICE_EN", "en.onnx")
    monkeypatch.setattr(settings, "PIPER_BIN", "definitely-not-installed-piper")
    assert tts.availability()["English"] is False                          # voice file alone is not enough
    monkeypatch.setattr(settings, "PIPER_BIN", "python3")
    assert tts.availability()["English"] is True and tts.availability()["Hindi"] is False
