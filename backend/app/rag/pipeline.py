"""RAG pipeline: ingest -> chunk -> embed -> FAISS index; permission-filtered retrieval; grounded answers."""
from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from sqlalchemy.orm import Session

from app.ai.llm_provider import LLMError, LLMUnavailable, llm_provider
from app.auth import permissions as perm
from app.config import settings
from app.models.models import (
    Document, DocumentChunk, LearningMaterial, Student, User, utcnow,
)
from app.rag import embeddings
from app.rag.chunker import chunk_segments
from app.rag.document_parser import ParseError, parse_file
from app.rag.vector_store import index

log = logging.getLogger("rag")

MESSAGES = {
    "insufficient": {
        "English": "I couldn't find this in your course materials, so I can't answer it reliably. Ask your faculty to upload notes for this topic, or try rephrasing your question.",
        "Hindi": "मुझे आपकी पाठ्य सामग्री में इसका उत्तर नहीं मिला, इसलिए मैं भरोसेमंद उत्तर नहीं दे सकता। कृपया अपने शिक्षक से इस विषय के नोट्स अपलोड करने को कहें या प्रश्न को दूसरे शब्दों में पूछें।",
        "Tamil": "உங்கள் பாடப் பொருட்களில் இதற்கான பதில் கிடைக்கவில்லை, எனவே நம்பகமான பதிலை வழங்க முடியாது. இந்தத் தலைப்புக்கான குறிப்புகளை பதிவேற்றுமாறு உங்கள் ஆசிரியரிடம் கேளுங்கள் அல்லது கேள்வியை வேறு வார்த்தைகளில் கேளுங்கள்.",
        "Kannada": "ನಿಮ್ಮ ಕೋರ್ಸ್ ಸಾಮಗ್ರಿಗಳಲ್ಲಿ ಇದಕ್ಕೆ ಉತ್ತರ ಸಿಗಲಿಲ್ಲ, ಆದ್ದರಿಂದ ವಿಶ್ವಾಸಾರ್ಹ ಉತ್ತರ ನೀಡಲು ಸಾಧ್ಯವಿಲ್ಲ. ಈ ವಿಷಯದ ಟಿಪ್ಪಣಿಗಳನ್ನು ಅಪ್‌ಲೋಡ್ ಮಾಡುವಂತೆ ನಿಮ್ಮ ಅಧ್ಯಾಪಕರನ್ನು ಕೇಳಿ ಅಥವಾ ಪ್ರಶ್ನೆಯನ್ನು ಬೇರೆ ರೀತಿಯಲ್ಲಿ ಕೇಳಿ.",
        "Malayalam": "നിങ്ങളുടെ കോഴ്‌സ് മെറ്റീരിയലുകളിൽ ഇതിനുള്ള ഉത്തരം കണ്ടെത്താനായില്ല, അതിനാൽ വിശ്വസനീയമായ ഉത്തരം നൽകാൻ കഴിയില്ല. ഈ വിഷയത്തിലെ കുറിപ്പുകൾ അപ്‌ലോഡ് ചെയ്യാൻ അധ്യാപകരോട് ആവശ്യപ്പെടുക, അല്ലെങ്കിൽ ചോദ്യം മറ്റൊരു രീതിയിൽ ചോദിക്കുക.",
    },
    "extractive": {
        "English": "The AI model is offline, so here are the most relevant excerpts from your course materials:",
        "Hindi": "AI मॉडल अभी बंद है, इसलिए आपकी पाठ्य सामग्री के सबसे प्रासंगिक अंश यहाँ हैं:",
        "Tamil": "AI மாதிரி தற்போது இயங்கவில்லை, எனவே உங்கள் பாடப் பொருட்களில் மிகவும் தொடர்புடைய பகுதிகள் இதோ:",
        "Kannada": "AI ಮಾದರಿ ಪ್ರಸ್ತುತ ಲಭ್ಯವಿಲ್ಲ, ಆದ್ದರಿಂದ ನಿಮ್ಮ ಕೋರ್ಸ್ ಸಾಮಗ್ರಿಗಳಿಂದ ಅತ್ಯಂತ ಸಂಬಂಧಿತ ಭಾಗಗಳು ಇಲ್ಲಿವೆ:",
        "Malayalam": "AI മോഡൽ ഇപ്പോൾ ലഭ്യമല്ല, അതിനാൽ നിങ്ങളുടെ കോഴ്‌സ് മെറ്റീരിയലുകളിലെ ഏറ്റവും പ്രസക്തമായ ഭാഗങ്ങൾ ഇതാ:",
    },
}
SUPPORTED_LANGUAGES = ["English", "Hindi", "Tamil", "Kannada", "Malayalam"]


def _msg(kind: str, language: str) -> str:
    return MESSAGES[kind].get(language, MESSAGES[kind]["English"])


# ====================================================================== index management
def _vec_to_bytes(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def _bytes_to_vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def ensure_index(db: Session, force: bool = False) -> None:
    """Build the FAISS index from the DB.  Chunks embedded with a different model are re-embedded."""
    emb = embeddings.get_embedder()
    if index.loaded and not force and index.model == emb.name:
        return
    index.reset(emb.dim, emb.name)
    stale = db.query(DocumentChunk).filter(
        (DocumentChunk.embedding.is_(None)) | (DocumentChunk.embedding_model != emb.name)).all()
    for i in range(0, len(stale), 64):
        batch = stale[i:i + 64]
        vecs = emb.embed_passages([c.content for c in batch])
        for c, v in zip(batch, vecs):
            c.embedding, c.embedding_model = _vec_to_bytes(v), emb.name
        db.commit()
    ids, vecs = [], []
    for c in db.query(DocumentChunk.id, DocumentChunk.embedding).filter(DocumentChunk.embedding_model == emb.name):
        ids.append(c.id)
        vecs.append(_bytes_to_vec(c.embedding))
    if ids:
        index.add(ids, np.vstack(vecs))
    log.info("Vector index ready: %d chunks, backend=%s, model=%s", index.size, index.backend, emb.name)


# ====================================================================== ingestion
def delete_material_chunks(db: Session, material_id: int) -> None:
    chunk_ids = [c.id for c in db.query(DocumentChunk.id).filter(DocumentChunk.material_id == material_id)]
    if chunk_ids:
        index.remove(chunk_ids)
    docs = db.query(Document).filter(Document.material_id == material_id).all()
    for d in docs:
        db.delete(d)          # cascades to chunks
    db.commit()


def process_material(db: Session, material_id: int) -> Dict[str, Any]:
    """Parse -> chunk -> embed -> index.  Safe to call again (re-processing replaces old chunks)."""
    mat = db.query(LearningMaterial).filter(LearningMaterial.id == material_id).first()
    if not mat:
        raise ValueError("material not found")
    ensure_index(db)
    delete_material_chunks(db, material_id)
    mat.status, mat.error_message = "processing", None
    db.commit()

    ext = Path(mat.file_name or mat.file_path or "").suffix.lower()
    doc = Document(material_id=mat.id, filename=mat.file_name or "file", file_type=ext.lstrip("."),
                   file_path=mat.file_path or "", status="processing")
    db.add(doc)
    db.commit()
    try:
        parsed = parse_file(mat.file_path, ext)
        chunks = chunk_segments(parsed.segments, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
        if not chunks:
            raise ParseError("The document contained too little text to index.")
        emb = embeddings.get_embedder()
        vecs = emb.embed_passages([c["content"] for c in chunks])
        rows = []
        for c, v in zip(chunks, vecs):
            row = DocumentChunk(
                document_id=doc.id, material_id=mat.id, subject_id=mat.subject_id, unit_id=mat.unit_id,
                topic_id=mat.topic_id, chunk_index=c["chunk_index"], content=c["content"], section=c.get("section"),
                page_ref=c.get("page_ref"), modality=c.get("modality", "text"), embedding=_vec_to_bytes(v),
                embedding_model=emb.name)
            db.add(row)
            rows.append(row)
        db.flush()
        index.add([r.id for r in rows], vecs)
        doc.status, doc.num_chunks, doc.modalities = "processed", len(rows), parsed.modalities
        mat.status, mat.processed_at = "indexed", utcnow()
        mat.content_text = parsed.text[:20000]
        mat.error_message = "; ".join(parsed.warnings)[:1000] or None
        db.commit()
        return {"status": "indexed", "num_chunks": len(rows), "pages": parsed.page_count,
                "modalities": parsed.modalities, "warnings": parsed.warnings}
    except ParseError as exc:
        _fail(db, mat, doc, str(exc))
        return {"status": "error", "message": str(exc)}
    except Exception as exc:  # unexpected - never leak internals, but keep the log
        log.exception("material %s processing failed", material_id)
        _fail(db, mat, doc, f"Processing failed ({type(exc).__name__}). Check the server log and try reprocessing.")
        return {"status": "error", "message": mat.error_message}


def _fail(db: Session, mat: LearningMaterial, doc: Document, message: str) -> None:
    db.rollback()
    mat = db.merge(mat)
    doc = db.merge(doc)
    ids = [c.id for c in db.query(DocumentChunk.id).filter(DocumentChunk.document_id == doc.id)]
    index.remove(ids)
    for c in db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id):
        db.delete(c)
    mat.status, mat.error_message = "error", message
    doc.status, doc.error_message, doc.num_chunks = "error", message, 0
    db.commit()


# ====================================================================== authorised retrieval
def authorized_subject_ids(db: Session, user: User) -> set:
    if user.role == "student":
        return perm.student_subject_ids(db, perm.student_of(db, user))
    if user.role == "faculty":
        return perm.faculty_subject_ids(db, perm.faculty_of(db, user))
    raise perm.forbidden("The learning assistant is available to students and faculty only.")


def allowed_chunk_ids(db: Session, user: User, subject_id: Optional[int], topic_id: Optional[int]) -> set:
    subjects = authorized_subject_ids(db, user)
    if subject_id is not None and subject_id not in subjects:
        raise perm.forbidden("You are not authorised to query this subject's materials.")
    q = db.query(DocumentChunk.id).join(LearningMaterial, LearningMaterial.id == DocumentChunk.material_id)
    q = q.filter(LearningMaterial.subject_id.in_([subject_id] if subject_id is not None else (subjects or {-1})))
    q = q.filter(LearningMaterial.status == "indexed")
    if user.role == "student":
        st = perm.student_of(db, user)
        q = q.filter(LearningMaterial.is_approved.is_(True))
        q = q.filter((LearningMaterial.class_id.is_(None)) | (LearningMaterial.class_id == st.class_id))
    if topic_id is not None:
        q = q.filter(LearningMaterial.topic_id == topic_id)
    return {r.id for r in q}


def retrieve(db: Session, user: User, query: str, subject_id: Optional[int] = None, topic_id: Optional[int] = None,
             top_k: Optional[int] = None) -> List[Dict[str, Any]]:
    ensure_index(db)
    allowed = allowed_chunk_ids(db, user, subject_id, topic_id)
    if not allowed:
        return []
    emb = embeddings.get_embedder()
    hits = index.search(emb.embed_query(query), top_k or settings.RAG_TOP_K, allowed)
    thr = embeddings.min_score()
    hits = [(cid, s) for cid, s in hits if s >= thr]
    if not hits:
        return []
    rows = {c.id: c for c in db.query(DocumentChunk).filter(DocumentChunk.id.in_([h[0] for h in hits]))}
    mats = {m.id: m for m in db.query(LearningMaterial).filter(
        LearningMaterial.id.in_({r.material_id for r in rows.values()}))}
    out = []
    for cid, score in hits:
        c = rows.get(cid)
        if not c:
            continue
        m = mats.get(c.material_id)
        out.append({"chunk_id": c.id, "material_id": c.material_id, "subject_id": c.subject_id, "topic_id": c.topic_id,
                    "title": m.title if m else "Course material", "filename": m.file_name if m else None,
                    "page_ref": c.page_ref, "section": c.section, "modality": c.modality, "score": round(score, 4),
                    "content": c.content})
    return out


def retrieve_for_topic(db: Session, topic_id: int, limit_chars: int = 9000) -> List[Dict[str, Any]]:
    """Approved, indexed chunks of one topic (used by question generation, called after the caller has
    already verified the faculty is assigned to the subject)."""
    q = (db.query(DocumentChunk, LearningMaterial).join(LearningMaterial, LearningMaterial.id == DocumentChunk.material_id)
         .filter(LearningMaterial.topic_id == topic_id, LearningMaterial.status == "indexed",
                 LearningMaterial.is_approved.is_(True)).order_by(DocumentChunk.material_id, DocumentChunk.chunk_index))
    out, total = [], 0
    for c, m in q:
        if total + len(c.content) > limit_chars:
            break
        total += len(c.content)
        out.append({"chunk_id": c.id, "material_id": m.id, "title": m.title, "page_ref": c.page_ref,
                    "section": c.section, "content": c.content})
    return out


# ====================================================================== answering
_CITE = re.compile(r"\[(\d{1,3})\]")


def check_citations(text: str, n_sources: int) -> Dict[str, Any]:
    """Validate [n] markers in a generated answer against the passages that were actually retrieved.
    Markers pointing at passages that do not exist are removed (never shown as if they were real sources)."""
    found = [int(m) for m in _CITE.findall(text)]
    bad = sorted({k for k in found if not 1 <= k <= n_sources})
    if bad:
        text = re.sub(r"\s?\[(\d{1,3})\]", lambda m: m.group(0) if 1 <= int(m.group(1)) <= n_sources else "", text)
    cited = sorted({k for k in found if 1 <= k <= n_sources})
    warning = None
    if bad:
        warning = f"Removed citation(s) {bad} that did not match any retrieved passage."
    elif not cited:
        warning = "The model did not cite any passage; treat this answer with care and check the sources."
    return {"text": text, "cited": cited, "warning": warning}


def _trim(text: str, limit: int) -> str:
    """Cut at a sentence (or word) boundary instead of mid-word."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "), cut.rfind("\n"))
    if end < limit * 0.5:
        end = cut.rfind(" ")
    return (cut[:end + 1] if end > 0 else cut).rstrip() + " …"


def _source_view(i: int, h: Dict[str, Any]) -> Dict[str, Any]:
    return {"n": i, "chunk_id": h["chunk_id"], "material_id": h["material_id"], "title": h["title"],
            "filename": h["filename"], "page_ref": h["page_ref"], "section": h["section"],
            "relevance": h["score"], "snippet": _trim(h["content"], 240)}


def answer_query(db: Session, user: User, query: str, subject_id: Optional[int], topic_id: Optional[int],
                 language: str = "English", history: Optional[List[Dict[str, str]]] = None,
                 allow_general: bool = False) -> Dict[str, Any]:
    language = language if language in SUPPORTED_LANGUAGES else "English"
    hits = retrieve(db, user, query, subject_id, topic_id)
    sources = [_source_view(i + 1, h) for i, h in enumerate(hits)]
    emb = embeddings.get_embedder()
    base = {"query": query, "language": language, "sources": sources, "embedding_backend": emb.name}

    if not hits:
        if allow_general and llm_provider.available():
            try:
                text = llm_provider.chat([
                    {"role": "system", "content": f"You are a careful tutor. Answer briefly in {language}. Start by saying "
                                                  "that this answer is general knowledge and NOT from the course materials."},
                    {"role": "user", "content": query}])
                return {**base, "answer": text, "answer_type": "general", "is_rag_grounded": False,
                        "provider": f"Ollama ({llm_provider.model})"}
            except (LLMError, LLMUnavailable):
                pass
        return {**base, "answer": _msg("insufficient", language), "answer_type": "insufficient_material",
                "is_rag_grounded": False, "provider": "retrieval"}

    context = "\n\n".join(f"[{s['n']}] ({s['title']}{', ' + s['page_ref'] if s['page_ref'] else ''})\n{h['content']}"
                          for s, h in zip(sources, hits))
    system = (
        "You are a friendly, accurate learning companion for college students. Answer ONLY using the numbered "
        "course passages provided. Cite passages like [1] or [2] after the statements they support. If the passages "
        f"do not contain the answer, say so plainly. Do not invent facts. Write your answer in {language}. "
        "Keep it clear and educational.")
    msgs = [{"role": "system", "content": system}]
    for turn in (history or [])[-6:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            msgs.append({"role": turn["role"], "content": turn["content"][:1500]})
    msgs.append({"role": "user", "content": f"Course passages:\n{context}\n\nQuestion: {query}"})
    try:
        text = llm_provider.chat(msgs)
        if text and text.strip():
            chk = check_citations(text, len(sources))
            return {**base, "answer": chk["text"], "answer_type": "grounded", "is_rag_grounded": True,
                    "cited_sources": chk["cited"], "citation_warning": chk["warning"],
                    "provider": f"Ollama ({llm_provider.model})"}
    except (LLMUnavailable, LLMError) as exc:
        log.info("LLM unavailable for RAG answer: %s", exc)

    excerpt = "\n\n".join(f"[{s['n']}] {_trim(h['content'], 500)}" for s, h in zip(sources, hits))
    return {**base, "answer": f"{_msg('extractive', language)}\n\n{excerpt}", "answer_type": "extractive",
            "is_rag_grounded": True, "provider": "extractive (no LLM)"}
