"""AI companion (RAG), capabilities, speech-to-text for the companion, text-to-speech."""
from __future__ import annotations

import datetime
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.ai.llm_provider import llm_provider
from app.auth import permissions as perm
from app.auth.dependencies import get_current_user, require_roles
from app.config import settings
from app.database import get_db
from app.models.models import AIConversation, DocumentChunk, LearningEvent, LearningMaterial, User, utcnow
from app.rag import embeddings, pipeline
from app.rag.document_parser import ocr_available, ocr_languages
from app.schemas.schemas import CompanionRequest, TTSRequest
from app.security.ratelimit import limiter
from app.speech import stt, tts

router = APIRouter(prefix="/rag", tags=["AI companion"])


@router.get("/capabilities")
def capabilities(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Honest report of what is actually available on this server."""
    llm = llm_provider.status()
    emb = embeddings.get_embedder()
    return {"llm": {"available": llm["available"], "model": settings.OLLAMA_MODEL, "error": llm.get("error")},
            "embeddings": {"backend": emb.name, "semantic": not emb.name.startswith("hashing"),
                           "note": "Lexical fallback in use: matches words, not meaning." if emb.name.startswith("hashing") else None},
            "indexed_chunks": db.query(DocumentChunk).count() if user.role in ("admin", "faculty") else None,
            "ocr": ocr_available(), "ocr_languages": ocr_languages() if ocr_available() else None, "speech_to_text": stt.is_available(), "tts": tts.availability(),
            "languages": list(pipeline.SUPPORTED_LANGUAGES)}


@router.post("/companion")
def companion(body: CompanionRequest, user: User = Depends(require_roles("student")), db: Session = Depends(get_db)):
    limiter.check(f"companion:{user.id}", 30, 300)
    st = perm.student_of(db, user)
    if body.subject_id:
        perm.require_student_subject(db, st, body.subject_id)
    sid = body.session_id or uuid.uuid4().hex[:16]
    history = []
    if st.conversation_retention_days != 0:
        prev = db.query(AIConversation).filter_by(student_id=st.id, session_id=sid).order_by(AIConversation.id.desc()).limit(3).all()
        for c in reversed(prev):
            history += [{"role": "user", "content": c.student_query}, {"role": "assistant", "content": c.ai_response}]
    res = pipeline.answer_query(db, user, body.message, body.subject_id, body.topic_id, body.language, history,
                                body.allow_general)
    if st.conversation_retention_days != 0:
        db.add(AIConversation(student_id=st.id, session_id=sid, subject_id=body.subject_id, topic_id=body.topic_id,
                              student_query=body.message, ai_response=res["answer"], language=body.language,
                              rag_sources_json=[{k: s[k] for k in ("n", "title", "page_ref", "material_id")} for s in res["sources"]],
                              answer_type=res.get("answer_type"), is_voice_query=body.is_voice))
    db.add(LearningEvent(student_id=st.id, event_type="companion_query", subject_id=body.subject_id, topic_id=body.topic_id,
                         metadata_json={"answer_type": res.get("answer_type"), "language": body.language}))
    db.commit()
    return {**res, "session_id": sid, "stored": st.conversation_retention_days != 0}


@router.get("/conversations")
def conversations(session_id: str = "", user: User = Depends(require_roles("student")), db: Session = Depends(get_db)):
    st = perm.student_of(db, user)
    q = db.query(AIConversation).filter_by(student_id=st.id)
    if session_id:
        q = q.filter_by(session_id=session_id)
    return [{"id": c.id, "session_id": c.session_id, "query": c.student_query, "answer": c.ai_response,
             "language": c.language, "sources": c.rag_sources_json, "answer_type": c.answer_type,
             "at": c.timestamp.isoformat() + "Z"} for c in q.order_by(AIConversation.id.desc()).limit(100)]


@router.delete("/conversations")
def clear_conversations(user: User = Depends(require_roles("student")), db: Session = Depends(get_db)):
    st = perm.student_of(db, user)
    n = db.query(AIConversation).filter_by(student_id=st.id).delete()
    db.commit()
    return {"deleted": n}


@router.post("/transcribe")
def transcribe(language: str = Form("English"), audio: UploadFile = File(...),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Voice question for the companion: audio -> text (faster-whisper). Audio is deleted right away."""
    limiter.check(f"stt:{user.id}", 30, 300)
    ext = Path(audio.filename or "a.webm").suffix.lower()
    if ext not in {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4"}:
        raise HTTPException(415, "Unsupported audio type.")
    folder = settings.UPLOAD_DIR / "tmp"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}{ext}"
    size = 0
    with open(path, "wb") as out:
        while chunk := audio.file.read(1024 * 256):
            size += len(chunk)
            if size > settings.MAX_AUDIO_ANSWER_MB * 1024 * 1024:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, "Audio too large.")
            out.write(chunk)
    try:
        tr = stt.transcribe_file(str(path), language)
    except stt.SpeechUnavailable as exc:
        raise HTTPException(503, f"{exc} Please type your question.")
    finally:
        path.unlink(missing_ok=True)
    return {"text": tr.text, "language": tr.language, "engine": tr.engine}


@router.post("/tts")
def speak(body: TTSRequest, user: User = Depends(get_current_user)):
    limiter.check(f"tts:{user.id}", 40, 300)
    try:
        return Response(tts.synthesize(body.text, body.language), media_type="audio/wav")
    except tts.TTSUnavailable as exc:
        raise HTTPException(503, f"{exc} The browser's own speech voices can be used instead.")
