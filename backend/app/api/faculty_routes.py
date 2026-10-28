"""Faculty: curriculum, learning materials (RAG ingestion), publishing today's topic."""
import io
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth import permissions as perm
from app.auth.dependencies import get_current_user, require_roles
from app.config import settings
from app.database import get_db
from app.models.models import (
    ClassGroup, DocumentChunk, Faculty, LearningMaterial, Student, Subject, Topic, Unit, User,
)
from app.rag import pipeline
from app.rag.document_parser import ALLOWED_EXT, DOC_EXT, IMAGE_EXT, MEDIA_EXT, TEXT_EXT
from app.schemas.schemas import MaterialUpdate, TodayTopicRequest, TopicIn, UnitIn
from app.security.audit import audit

router = APIRouter(prefix="/faculty", tags=["Faculty"])
FacultyOnly = Depends(require_roles("faculty"))
MATERIAL_TYPES = {"syllabus", "notes", "slides", "reference", "question_bank", "lecture_media", "scan"}


def _fac(db, user) -> Faculty:
    return perm.faculty_of(db, user)


def _topic_chain(db: Session, subject_id: int, unit_id: int, topic_id: int):
    unit = db.query(Unit).filter_by(id=unit_id, subject_id=subject_id).first()
    topic = db.query(Topic).filter_by(id=topic_id, unit_id=unit_id).first() if unit else None
    if not unit or not topic:
        raise HTTPException(422, "The unit/topic does not belong to this subject.")
    return unit, topic


def _check_class(db: Session, faculty: Faculty, subject: Subject, class_id: Optional[int]) -> None:
    if class_id is None:
        return
    if not db.query(ClassGroup).filter_by(id=class_id).first():
        raise HTTPException(422, "Unknown class.")
    all_classes, ids = perm.faculty_class_scope(db, faculty, subject)
    if not all_classes and class_id not in ids:
        raise perm.forbidden("You are not assigned to teach this subject to that class.")


def material_view(m: LearningMaterial) -> dict:
    return {"id": m.id, "title": m.title, "subject_id": m.subject_id, "unit_id": m.unit_id, "topic_id": m.topic_id,
            "class_id": m.class_id, "material_type": m.material_type, "file_name": m.file_name, "file_size": m.file_size,
            "status": m.status, "error_message": m.error_message, "is_approved": m.is_approved,
            "modalities": [d.modalities for d in m.documents][:1][0] if m.documents and m.documents[0].modalities else [],
            "chunks": sum(d.num_chunks or 0 for d in m.documents),
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "processed_at": m.processed_at.isoformat() if m.processed_at else None}


# ------------------------------------------------------------------ subjects & curriculum
@router.get("/subjects")
def my_subjects(user: User = FacultyOnly, db: Session = Depends(get_db)):
    f = _fac(db, user)
    out = []
    for s in db.query(Subject).filter(Subject.id.in_(perm.faculty_subject_ids(db, f) or {-1})).order_by(Subject.code):
        all_classes, ids = perm.faculty_class_scope(db, f, s)
        classes = db.query(ClassGroup).filter(ClassGroup.department_code == s.department) if all_classes else \
            db.query(ClassGroup).filter(ClassGroup.id.in_(ids or {-1}))
        mats = db.query(LearningMaterial).filter_by(subject_id=s.id).all()
        by_topic = {}
        for m in mats:
            by_topic[m.topic_id] = by_topic.get(m.topic_id, 0) + 1
        out.append({
            "id": s.id, "code": s.code, "name": s.name, "department": s.department, "semester": s.semester,
            "classes": [{"id": c.id, "name": c.name} for c in classes],
            "units": [{"id": u.id, "unit_number": u.unit_number, "title": u.title, "topics": [
                {"id": t.id, "title": t.title, "order_index": t.order_index, "is_today_topic": t.is_today_topic,
                 "today_class_id": t.today_class_id, "materials_count": by_topic.get(t.id, 0)} for t in u.topics]}
                for u in s.units]})
    return out


@router.post("/subjects/{subject_id}/units", status_code=201)
def add_unit(subject_id: int, body: UnitIn, request: Request, user: User = FacultyOnly, db: Session = Depends(get_db)):
    perm.require_subject_faculty(db, user, subject_id)
    if db.query(Unit).filter_by(subject_id=subject_id, unit_number=body.unit_number).first():
        raise HTTPException(409, "That unit number already exists.")
    u = Unit(subject_id=subject_id, unit_number=body.unit_number, title=body.title, description=body.description)
    db.add(u)
    db.commit()
    audit(db, "UNIT_CREATED", "unit", u.id, user=user, request=request)
    return {"id": u.id}


@router.post("/units/{unit_id}/topics", status_code=201)
def add_topic(unit_id: int, body: TopicIn, request: Request, user: User = FacultyOnly, db: Session = Depends(get_db)):
    unit = db.query(Unit).filter_by(id=unit_id).first()
    if not unit:
        raise perm.not_found("Unit")
    perm.require_subject_faculty(db, user, unit.subject_id)
    order = body.order_index or (max([t.order_index or 0 for t in unit.topics] or [0]) + 1)
    t = Topic(unit_id=unit_id, title=body.title, description=body.description, order_index=order)
    db.add(t)
    db.commit()
    audit(db, "TOPIC_CREATED", "topic", t.id, user=user, request=request)
    return {"id": t.id}


@router.patch("/topics/{topic_id}")
def edit_topic(topic_id: int, body: TopicIn, user: User = FacultyOnly, db: Session = Depends(get_db)):
    t = db.query(Topic).filter_by(id=topic_id).first()
    if not t:
        raise perm.not_found("Topic")
    perm.require_subject_faculty(db, user, t.unit.subject_id)
    t.title = body.title
    if body.description is not None:
        t.description = body.description
    if body.order_index:
        t.order_index = body.order_index
    db.commit()
    return {"status": "ok"}


@router.post("/today-topic")
def set_today_topic(body: TodayTopicRequest, request: Request, user: User = FacultyOnly, db: Session = Depends(get_db)):
    """Select today's topic (e.g. NLP -> Unit 5 -> Hobbs algorithm) and publish it to a class."""
    topic = db.query(Topic).filter_by(id=body.topic_id).first()
    if not topic:
        raise perm.not_found("Topic")
    subject = perm.require_subject_faculty(db, user, topic.unit.subject_id)
    _check_class(db, _fac(db, user), subject, body.class_id)
    for u in subject.units:
        for t in u.topics:
            if t.is_today_topic and t.today_class_id == body.class_id:
                t.is_today_topic, t.today_date, t.today_class_id = False, None, None
    if body.is_today:
        topic.is_today_topic, topic.today_class_id = True, body.class_id
        from app.models.models import utcnow
        topic.today_date = utcnow()
    db.commit()
    audit(db, "TODAY_TOPIC_SET", "topic", topic.id, user=user, request=request,
          details={"class_id": body.class_id, "on": body.is_today})
    return {"status": "ok", "topic_id": topic.id, "is_today_topic": topic.is_today_topic}


@router.get("/classes")
def my_classes(subject_id: int, user: User = FacultyOnly, db: Session = Depends(get_db)):
    s = perm.require_subject_faculty(db, user, subject_id)
    all_classes, ids = perm.faculty_class_scope(db, _fac(db, user), s)
    q = db.query(ClassGroup).filter(ClassGroup.department_code == s.department) if all_classes else \
        db.query(ClassGroup).filter(ClassGroup.id.in_(ids or {-1}))
    return [{"id": c.id, "name": c.name, "semester": c.semester} for c in q]


# ------------------------------------------------------------------ materials
def _validate_upload(ext: str, data_head: bytes, path: Path, size: int) -> None:
    if ext not in ALLOWED_EXT:
        raise HTTPException(415, f"File type '{ext}' is not allowed. Allowed: PDF, DOCX, PPTX, TXT, MD, images, audio/video.")
    limit = settings.MAX_MEDIA_UPLOAD_MB if ext in MEDIA_EXT else settings.MAX_UPLOAD_MB
    if size > limit * 1024 * 1024:
        raise HTTPException(413, f"File is larger than the {limit} MB limit.")
    if size == 0:
        raise HTTPException(422, "The file is empty.")
    if ext == ".pdf" and not data_head.startswith(b"%PDF"):
        raise HTTPException(415, "The file is not a valid PDF.")
    if ext in (".docx", ".pptx"):
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
            if "[Content_Types].xml" not in names or not any(n.startswith("word/" if ext == ".docx" else "ppt/") for n in names):
                raise ValueError
        except Exception:
            raise HTTPException(415, f"The file is not a valid {ext[1:].upper()} document.")
    if ext in TEXT_EXT and b"\x00" in data_head:
        raise HTTPException(415, "The text file contains binary data.")
    if ext in IMAGE_EXT:
        try:
            from PIL import Image
            with Image.open(path) as im:
                im.verify()
        except Exception:
            raise HTTPException(415, "The image could not be read.")


@router.post("/materials/upload", status_code=201)
def upload_material(request: Request, subject_id: int = Form(...), unit_id: int = Form(...), topic_id: int = Form(...),
                    title: str = Form(..., min_length=2, max_length=255), material_type: str = Form("notes"),
                    class_id: Optional[int] = Form(None), file: UploadFile = File(...),
                    user: User = FacultyOnly, db: Session = Depends(get_db)):
    from app.security.ratelimit import limiter, client_ip
    limiter.check(f"upload:{user.id}", 30, 600)
    subject = perm.require_subject_faculty(db, user, subject_id)
    fac = _fac(db, user)
    _topic_chain(db, subject_id, unit_id, topic_id)
    _check_class(db, fac, subject, class_id)
    if material_type not in MATERIAL_TYPES:
        raise HTTPException(422, f"material_type must be one of {sorted(MATERIAL_TYPES)}")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(415, f"File type '{ext}' is not allowed.")
    store_dir = settings.UPLOAD_DIR / "materials"
    store_dir.mkdir(parents=True, exist_ok=True)
    key = f"{uuid.uuid4().hex}{ext}"
    path = store_dir / key
    limit = (settings.MAX_MEDIA_UPLOAD_MB if ext in MEDIA_EXT else settings.MAX_UPLOAD_MB) * 1024 * 1024
    size, head = 0, b""
    with open(path, "wb") as out:                       # stream to disk with a hard cap
        while chunk := file.file.read(1024 * 1024):
            if not head:
                head = chunk[:16]
            size += len(chunk)
            if size > limit:
                out.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, "File is too large.")
            out.write(chunk)
    try:
        _validate_upload(ext, head, path, size)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    from app.security.crypto import sha256_hex
    mat = LearningMaterial(subject_id=subject_id, unit_id=unit_id, topic_id=topic_id, faculty_id=fac.id, class_id=class_id,
                           title=title, material_type=material_type, file_path=str(path),
                           file_name=Path(file.filename).name[:255], file_size=size, content_type=file.content_type,
                           sha256=sha256_hex(path.read_bytes()) if size < 50 * 1024 * 1024 else None, status="uploaded",
                           is_approved=True)
    db.add(mat)
    db.commit()
    result = pipeline.process_material(db, mat.id)
    db.refresh(mat)
    audit(db, "MATERIAL_UPLOADED", "material", mat.id, user=user, request=request,
          details={"status": mat.status, "type": ext})
    return {**material_view(mat), "indexing": result}


@router.get("/materials")
def list_materials(subject_id: int, topic_id: Optional[int] = None, user: User = FacultyOnly, db: Session = Depends(get_db)):
    perm.require_subject_faculty(db, user, subject_id)
    q = db.query(LearningMaterial).filter_by(subject_id=subject_id)
    if topic_id:
        q = q.filter_by(topic_id=topic_id)
    return [material_view(m) for m in q.order_by(LearningMaterial.id.desc())]


def _own_material(db: Session, user: User, material_id: int) -> LearningMaterial:
    m = db.query(LearningMaterial).filter_by(id=material_id).first()
    if not m:
        raise perm.not_found("Material")
    perm.require_subject_faculty(db, user, m.subject_id)
    return m


@router.post("/materials/{material_id}/reprocess")
def reprocess(material_id: int, request: Request, user: User = FacultyOnly, db: Session = Depends(get_db)):
    m = _own_material(db, user, material_id)
    if not m.file_path or not Path(m.file_path).exists():
        raise HTTPException(410, "The stored file is missing; please upload it again.")
    res = pipeline.process_material(db, m.id)
    audit(db, "MATERIAL_REPROCESSED", "material", m.id, user=user, request=request)
    db.refresh(m)
    return {**material_view(m), "indexing": res}


@router.patch("/materials/{material_id}")
def update_material(material_id: int, body: MaterialUpdate, request: Request, user: User = FacultyOnly,
                    db: Session = Depends(get_db)):
    m = _own_material(db, user, material_id)
    data = body.model_dump(exclude_unset=True)
    if "unit_id" in data or "topic_id" in data:
        unit_id, topic_id = data.get("unit_id", m.unit_id), data.get("topic_id", m.topic_id)
        _topic_chain(db, m.subject_id, unit_id, topic_id)
        m.unit_id, m.topic_id = unit_id, topic_id
        db.query(DocumentChunk).filter_by(material_id=m.id).update({"unit_id": unit_id, "topic_id": topic_id})
    if "class_id" in data:
        _check_class(db, _fac(db, user), db.query(Subject).filter_by(id=m.subject_id).first(), data["class_id"])
        m.class_id = data["class_id"]
    for k in ("title", "is_approved", "material_type"):
        if k in data and data[k] is not None:
            if k == "material_type" and data[k] not in MATERIAL_TYPES:
                raise HTTPException(422, "Invalid material type.")
            setattr(m, k, data[k])
    db.commit()
    audit(db, "MATERIAL_UPDATED", "material", m.id, user=user, request=request, details=list(data))
    return material_view(m)


@router.delete("/materials/{material_id}")
def delete_material(material_id: int, request: Request, user: User = FacultyOnly, db: Session = Depends(get_db)):
    m = _own_material(db, user, material_id)
    pipeline.delete_material_chunks(db, m.id)
    try:
        Path(m.file_path).unlink(missing_ok=True)
    except OSError:
        pass
    db.delete(m)
    db.commit()
    audit(db, "MATERIAL_DELETED", "material", material_id, user=user, request=request)
    return {"status": "deleted"}


@router.get("/materials/{material_id}/download")
def download_material(material_id: int, user: User = FacultyOnly, db: Session = Depends(get_db)):
    m = _own_material(db, user, material_id)
    if not m.file_path or not Path(m.file_path).exists():
        raise HTTPException(410, "File missing.")
    return FileResponse(m.file_path, filename=m.file_name, headers={"Content-Disposition": f'attachment; filename="{m.file_name}"'})
