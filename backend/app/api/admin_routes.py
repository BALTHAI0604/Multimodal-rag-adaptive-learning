import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.auth import permissions as perm
from app.auth.dependencies import require_roles
from app.auth.firestore_sync import sync_user
from app.auth.providers import AuthError, get_provider
from app.config import settings
from app.database import get_db
from app.models.models import (
    AIConversation, AuditLog, ClassGroup, Department, Faculty, Student, Subject, SubjectAssignment, User,
    VoiceAnswer, utcnow,
)
from app.schemas.schemas import (
    AdminCreateUser, AdminUpdateStudent, AdminUpdateUser, AssignmentIn, ClassIn, DepartmentIn, SubjectIn,
)
from app.security.audit import audit

router = APIRouter(prefix="/admin", tags=["Administration"])
Admin = Depends(require_roles("admin"))


def _user_row(db: Session, u: User) -> dict:
    d = {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "is_active": u.is_active,
         "email_verified": bool(u.email_verified), "created_at": u.created_at.isoformat() if u.created_at else None}
    if u.role == "student":
        s = db.query(Student).filter_by(user_id=u.id).first()
        if s:
            d["student"] = {"id": s.id, "roll_number": s.roll_number, "department": s.department, "semester": s.semester,
                            "class_id": s.class_id, "advisor_user_id": s.advisor_id}
    if u.role == "faculty":
        f = db.query(Faculty).filter_by(user_id=u.id).first()
        if f:
            d["faculty"] = {"id": f.id, "employee_id": f.employee_id, "department": f.department}
    return d


@router.post("/users", status_code=201)
def create_user(body: AdminCreateUser, request: Request, admin: User = Admin, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "An account with that e-mail already exists.")
    if body.role == "student" and not body.student:
        raise HTTPException(422, "Student details (roll_number, department, semester) are required.")
    if body.role == "faculty" and not body.faculty:
        raise HTTPException(422, "Faculty details (employee_id, department) are required.")
    if body.role == "student" and db.query(Student).filter(Student.roll_number.ilike(body.student.roll_number)).first():
        raise HTTPException(409, "That student ID is already registered.")
    if body.role == "faculty" and db.query(Faculty).filter(Faculty.employee_id.ilike(body.faculty.employee_id)).first():
        raise HTTPException(409, "That employee ID is already registered.")
    provider = get_provider()
    try:
        acct = provider.create_account(email, body.full_name, None)
    except AuthError as exc:
        raise HTTPException(exc.status, exc.message)
    user = User(email=email, username=email, full_name=body.full_name, role=body.role, firebase_uid=acct["uid"],
                hashed_password=acct["password_hash"], is_active=True, email_verified=(provider.name == "local"),
                created_by_id=admin.id)
    db.add(user)
    db.flush()
    if body.role == "student":
        sp = body.student
        cls = db.query(ClassGroup).filter_by(id=sp.class_id).first() if sp.class_id else None
        if sp.class_id and not cls:
            db.rollback()
            raise HTTPException(422, "Unknown class.")
        db.add(Student(user_id=user.id, roll_number=sp.roll_number, department=sp.department, semester=sp.semester,
                       class_id=sp.class_id, class_name=cls.name if cls else None,
                       advisor_id=sp.advisor_user_id or (cls.advisor_user_id if cls else None)))
    elif body.role == "faculty":
        fp = body.faculty
        db.add(Faculty(user_id=user.id, department=fp.department, designation=fp.designation, employee_id=fp.employee_id))
    db.commit()
    sync_user(user)
    if provider.name == "firebase":
        provider.send_password_reset(email)          # the user sets their own password via the e-mailed link
    audit(db, "USER_CREATED", "user", user.id, user=admin, request=request, details={"role": body.role})
    out = _user_row(db, user)
    if acct.get("temp_password"):
        out["temporary_password"] = acct["temp_password"]       # shown once; local development only
    out["invite"] = "password-setup e-mail sent" if provider.name == "firebase" else "temporary password shown once"
    return out


@router.get("/users")
def list_users(role: Optional[str] = None, q: Optional[str] = None, limit: int = Query(100, le=500),
               admin: User = Admin, db: Session = Depends(get_db)):
    qs = db.query(User)
    if role:
        qs = qs.filter(User.role == role)
    if q:
        like = f"%{q.lower()}%"
        qs = qs.filter((User.email.ilike(like)) | (User.full_name.ilike(like)))
    return [_user_row(db, u) for u in qs.order_by(User.id.desc()).limit(limit)]


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: AdminUpdateUser, request: Request, admin: User = Admin, db: Session = Depends(get_db)):
    u = db.query(User).filter_by(id=user_id).first()
    if not u:
        raise perm.not_found("User")
    changes = {}
    if body.role and body.role != u.role:
        if u.id == admin.id:
            raise HTTPException(422, "You cannot change your own role.")
        if body.role == "student" and not db.query(Student).filter_by(user_id=u.id).first():
            raise HTTPException(422, "Create the student profile first (delete and re-create the account).")
        if body.role == "faculty" and not db.query(Faculty).filter_by(user_id=u.id).first():
            raise HTTPException(422, "Create the faculty profile first (delete and re-create the account).")
        changes["role"] = [u.role, body.role]
        u.role = body.role
        u.token_valid_after = utcnow()          # force re-login so old tokens cannot carry old privileges
    if body.full_name:
        u.full_name = body.full_name
    if body.is_active is not None and body.is_active != u.is_active:
        if u.id == admin.id:
            raise HTTPException(422, "You cannot deactivate your own account.")
        if not body.is_active and u.role == "admin" and db.query(User).filter_by(role="admin", is_active=True).count() <= 1:
            raise HTTPException(422, "Cannot deactivate the last active administrator.")
        u.is_active = body.is_active
        u.deactivated_at = None if body.is_active else utcnow()
        if not body.is_active:
            u.token_valid_after = utcnow()
        try:
            get_provider().set_disabled(u.firebase_uid, not body.is_active)
            if not body.is_active:
                get_provider().revoke(u.firebase_uid)
        except Exception:
            pass
        changes["is_active"] = body.is_active
    db.commit()
    sync_user(u)
    audit(db, "USER_UPDATED", "user", u.id, user=admin, request=request, details=changes)
    return _user_row(db, u)


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(user_id: int, request: Request, admin: User = Admin, db: Session = Depends(get_db)):
    u = db.query(User).filter_by(id=user_id).first()
    if not u:
        raise perm.not_found("User")
    provider = get_provider()
    out = {"status": "ok"}
    if provider.name == "firebase":
        provider.send_password_reset(u.email)
        out["message"] = "A password-reset e-mail has been sent."
    else:
        acct = provider.create_account(u.email, u.full_name, None)
        u.hashed_password = acct["password_hash"]
        out["temporary_password"] = acct["temp_password"]
    u.token_valid_after = utcnow()
    u.failed_login_count, u.locked_until = 0, None
    db.commit()
    audit(db, "PASSWORD_RESET_BY_ADMIN", "user", u.id, user=admin, request=request)
    return out


@router.patch("/students/{student_id}")
def update_student(student_id: int, body: AdminUpdateStudent, request: Request, admin: User = Admin,
                   db: Session = Depends(get_db)):
    s = db.query(Student).filter_by(id=student_id).first()
    if not s:
        raise perm.not_found("Student")
    data = body.model_dump(exclude_unset=True)
    if "class_id" in data:
        cls = db.query(ClassGroup).filter_by(id=data["class_id"]).first() if data["class_id"] else None
        if data["class_id"] and not cls:
            raise HTTPException(422, "Unknown class.")
        s.class_id, s.class_name = data["class_id"], (cls.name if cls else None)
    if "advisor_user_id" in data:
        adv = db.query(User).filter_by(id=data["advisor_user_id"], role="advisor").first() if data["advisor_user_id"] else None
        if data["advisor_user_id"] and not adv:
            raise HTTPException(422, "Advisor must be an advisor account.")
        s.advisor_id = data["advisor_user_id"]
    for k in ("semester", "extra_time_percent", "exempt_fullscreen", "typed_voice_alternative"):
        if k in data and data[k] is not None:
            setattr(s, k, data[k])
    db.commit()
    audit(db, "STUDENT_UPDATED", "student", s.id, user=admin, request=request, details={k: v for k, v in data.items()})
    return {"status": "ok"}


# ------------------------------------------------------------------ structure
@router.get("/structure")
def structure(admin: User = Admin, db: Session = Depends(get_db)):
    return {
        "departments": [{"id": d.id, "code": d.code, "name": d.name} for d in db.query(Department).order_by(Department.code)],
        "classes": [{"id": c.id, "name": c.name, "department_code": c.department_code, "semester": c.semester,
                     "academic_year": c.academic_year, "advisor_user_id": c.advisor_user_id} for c in db.query(ClassGroup)],
        "subjects": [{"id": s.id, "code": s.code, "name": s.name, "department": s.department, "semester": s.semester}
                     for s in db.query(Subject).order_by(Subject.code)],
        "assignments": [{"id": a.id, "subject_id": a.subject_id, "faculty_id": a.faculty_id, "class_id": a.class_id}
                        for a in db.query(SubjectAssignment)],
        "faculty": [{"id": f.id, "name": f.user.full_name, "employee_id": f.employee_id} for f in db.query(Faculty)],
        "advisors": [{"id": u.id, "name": u.full_name} for u in db.query(User).filter_by(role="advisor", is_active=True)],
    }


@router.post("/departments", status_code=201)
def add_department(body: DepartmentIn, request: Request, admin: User = Admin, db: Session = Depends(get_db)):
    if db.query(Department).filter(Department.code.ilike(body.code)).first():
        raise HTTPException(409, "Department code already exists.")
    d = Department(code=body.code.upper(), name=body.name)
    db.add(d)
    db.commit()
    audit(db, "DEPARTMENT_CREATED", "department", d.id, user=admin, request=request)
    return {"id": d.id, "code": d.code, "name": d.name}


@router.post("/classes", status_code=201)
def add_class(body: ClassIn, request: Request, admin: User = Admin, db: Session = Depends(get_db)):
    if body.advisor_user_id and not db.query(User).filter_by(id=body.advisor_user_id, role="advisor").first():
        raise HTTPException(422, "Advisor must be an advisor account.")
    c = ClassGroup(name=body.name, department_code=body.department_code.upper(), semester=body.semester,
                   academic_year=body.academic_year, advisor_user_id=body.advisor_user_id)
    db.add(c)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(409, "That class already exists.")
    audit(db, "CLASS_CREATED", "class", c.id, user=admin, request=request)
    return {"id": c.id}


@router.patch("/classes/{class_id}/advisor")
def set_class_advisor(class_id: int, advisor_user_id: Optional[int], request: Request, admin: User = Admin,
                      db: Session = Depends(get_db)):
    c = db.query(ClassGroup).filter_by(id=class_id).first()
    if not c:
        raise perm.not_found("Class")
    if advisor_user_id and not db.query(User).filter_by(id=advisor_user_id, role="advisor").first():
        raise HTTPException(422, "Advisor must be an advisor account.")
    c.advisor_user_id = advisor_user_id
    db.commit()
    audit(db, "CLASS_ADVISOR_SET", "class", c.id, user=admin, request=request, details={"advisor": advisor_user_id})
    return {"status": "ok"}


@router.post("/subjects", status_code=201)
def add_subject(body: SubjectIn, request: Request, admin: User = Admin, db: Session = Depends(get_db)):
    if db.query(Subject).filter(Subject.code.ilike(body.code)).first():
        raise HTTPException(409, "Subject code already exists.")
    s = Subject(code=body.code.upper(), name=body.name, department=body.department.upper(), semester=body.semester,
                description=body.description)
    db.add(s)
    db.commit()
    audit(db, "SUBJECT_CREATED", "subject", s.id, user=admin, request=request)
    return {"id": s.id}


@router.post("/assignments", status_code=201)
def add_assignment(body: AssignmentIn, request: Request, admin: User = Admin, db: Session = Depends(get_db)):
    if not db.query(Subject).filter_by(id=body.subject_id).first() or not db.query(Faculty).filter_by(id=body.faculty_id).first():
        raise HTTPException(422, "Unknown subject or faculty.")
    if body.class_id and not db.query(ClassGroup).filter_by(id=body.class_id).first():
        raise HTTPException(422, "Unknown class.")
    if db.query(SubjectAssignment).filter_by(subject_id=body.subject_id, faculty_id=body.faculty_id, class_id=body.class_id).first():
        raise HTTPException(409, "Already assigned.")
    a = SubjectAssignment(subject_id=body.subject_id, faculty_id=body.faculty_id, class_id=body.class_id)
    db.add(a)
    db.commit()
    audit(db, "ASSIGNMENT_CREATED", "subject", body.subject_id, user=admin, request=request,
          details={"faculty_id": body.faculty_id, "class_id": body.class_id})
    return {"id": a.id}


@router.delete("/assignments/{assignment_id}")
def remove_assignment(assignment_id: int, request: Request, admin: User = Admin, db: Session = Depends(get_db)):
    a = db.query(SubjectAssignment).filter_by(id=assignment_id).first()
    if not a:
        raise perm.not_found("Assignment")
    db.delete(a)
    db.commit()
    audit(db, "ASSIGNMENT_REMOVED", "subject", a.subject_id, user=admin, request=request)
    return {"status": "ok"}


# ------------------------------------------------------------------ audit + system
@router.get("/audit-logs")
def audit_logs(action: Optional[str] = None, limit: int = Query(100, le=500), offset: int = 0,
               admin: User = Admin, request: Request = None, db: Session = Depends(get_db)):
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    rows = q.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all()
    return [{"id": r.id, "time": r.created_at.isoformat(), "user_id": r.user_id, "role": r.actor_role, "action": r.action,
             "resource": r.resource_type, "resource_id": r.resource_id, "outcome": r.outcome, "ip": r.ip_address,
             "details": r.details_json} for r in rows]


@router.get("/system")
def system_status(admin: User = Admin, db: Session = Depends(get_db)):
    from app.ai.llm_provider import llm_provider
    from app.blockchain import service as chain
    from app.rag import embeddings
    from app.rag.vector_store import index
    from app.speech import stt, tts
    emb = embeddings.get_embedder()
    return {
        "environment": settings.ENVIRONMENT, "auth_mode": settings.AUTH_MODE,
        "llm": llm_provider.status(force=True), "embedding": {"backend": emb.kind, "model": emb.name,
                                                               "fallback_reason": embeddings.fallback_reason},
        "vector_index": {"backend": index.backend, "chunks": index.size},
        "speech_to_text": {"available": stt.is_available(), "model": settings.WHISPER_MODEL},
        "text_to_speech": tts.availability(), "ocr": __import__("app.rag.document_parser", fromlist=["x"]).ocr_available(),
        "blockchain": chain.chain_status(),
    }


@router.post("/maintenance/purge")
def purge(request: Request, admin: User = Admin, db: Session = Depends(get_db)):
    """Applies retention: deletes old AI conversations and stored voice recordings."""
    import os
    cutoff = utcnow() - datetime.timedelta(days=settings.CONVERSATION_RETENTION_DAYS)
    n_conv = db.query(AIConversation).filter(AIConversation.timestamp < cutoff).delete()
    a_cut = utcnow() - datetime.timedelta(days=settings.VOICE_AUDIO_RETENTION_DAYS)
    n_audio = 0
    for v in db.query(VoiceAnswer).filter(VoiceAnswer.audio_file_path.isnot(None), VoiceAnswer.created_at < a_cut):
        try:
            os.remove(v.audio_file_path)
        except OSError:
            pass
        v.audio_file_path, v.audio_deleted_at = None, utcnow()
        n_audio += 1
    db.commit()
    audit(db, "RETENTION_PURGE", "system", None, user=admin, request=request,
          details={"conversations": n_conv, "recordings": n_audio})
    return {"conversations_deleted": n_conv, "recordings_deleted": n_audio}


@router.post("/rag/reindex")
def reindex(request: Request, admin: User = Admin, db: Session = Depends(get_db)):
    from app.rag.pipeline import ensure_index
    ensure_index(db, force=True)
    audit(db, "RAG_REINDEX", "system", None, user=admin, request=request)
    from app.rag.vector_store import index
    return {"chunks": index.size}
