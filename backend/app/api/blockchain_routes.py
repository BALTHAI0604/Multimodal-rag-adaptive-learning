"""Ledger integrity. Only hashes are ever on-chain; this API exposes only what the caller may see."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import permissions as perm
from app.auth.dependencies import get_current_user, require_roles
from app.blockchain import service as chain
from app.database import get_db
from app.models.models import AssessmentAttempt, BlockchainRecord, BlockchainVerification, Student, User
from app.schemas.schemas import VerifyRequest
from app.security.audit import audit
from app.security.ratelimit import limiter

router = APIRouter(prefix="/blockchain", tags=["Blockchain integrity"])


def _can_see(db: Session, user: User, rec: BlockchainRecord) -> bool:
    if user.role == "admin":
        return True
    st = db.query(Student).filter_by(id=rec.student_id).first()
    if user.role == "student":
        return bool(st and st.user_id == user.id)
    att = db.query(AssessmentAttempt).filter_by(id=rec.attempt_id).first()
    if user.role == "advisor":
        return rec.student_id in perm.advisor_student_ids(db, user)
    if user.role == "faculty" and att:
        try:
            perm.faculty_can_see_student(db, user, st, att.assessment.subject_id)
            return True
        except HTTPException:
            return False
    return False


def _view(r: BlockchainRecord) -> dict:
    return {"record_ref": r.record_ref, "version": r.version, "status": r.status, "attempt_id": r.attempt_id,
            "hash": r.payload_sha256_hash, "transaction_hash": r.transaction_hash, "block_number": r.block_number,
            "chain_id": r.chain_id, "contract": r.contract_address, "error": r.error_message,
            "last_verified": r.verified_status, "created_at": r.created_at.isoformat() + "Z"}


@router.get("/status")
def status(user: User = Depends(get_current_user)):
    s = chain.chain_status()
    if user.role not in ("admin", "management"):
        s = {"connected": s["connected"]}
    return s


@router.get("/records")
def records(user: User = Depends(require_roles("student", "faculty", "advisor", "admin")), db: Session = Depends(get_db)):
    q = db.query(BlockchainRecord).order_by(BlockchainRecord.id.desc())
    if user.role == "student":
        st = perm.student_of(db, user)
        q = q.filter_by(student_id=st.id)
    return [_view(r) for r in q.limit(200) if _can_see(db, user, r)][:100]


@router.post("/verify")
def verify(body: VerifyRequest, request: Request, user: User = Depends(require_roles("student", "faculty", "advisor", "admin")),
           db: Session = Depends(get_db)):
    limiter.check(f"verify:{user.id}", 30, 300)
    rec = db.query(BlockchainRecord).filter_by(record_ref=body.record_ref).first()
    if not rec or not _can_see(db, user, rec):
        raise perm.not_found("Record")
    res = chain.verify_record(db, rec, user.id)
    audit(db, "LEDGER_VERIFIED", "blockchain_record", rec.id, user=user, request=request, details={"result": res["result"]})
    return res


@router.get("/records/{record_ref}/history")
def history(record_ref: str, user: User = Depends(require_roles("student", "faculty", "advisor", "admin")),
            db: Session = Depends(get_db)):
    rec = db.query(BlockchainRecord).filter_by(record_ref=record_ref).first()
    if not rec or not _can_see(db, user, rec):
        raise perm.not_found("Record")
    rows = db.query(BlockchainVerification).filter_by(record_id=rec.id).order_by(BlockchainVerification.id.desc()).limit(50)
    return [{"result": v.result, "at": v.created_at.isoformat() + "Z", "by": v.verified_by_id} for v in rows]


@router.post("/records/{record_ref}/retry")
def retry(record_ref: str, request: Request, user: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    rec = db.query(BlockchainRecord).filter_by(record_ref=record_ref).first()
    if not rec:
        raise perm.not_found("Record")
    rec = chain.try_anchor(db, rec, recheck=True)
    audit(db, "LEDGER_RETRY", "blockchain_record", rec.id, user=user, request=request, details={"status": rec.status})
    return _view(rec)


@router.post("/retry-pending")
def retry_pending(request: Request, user: User = Depends(require_roles("admin")), db: Session = Depends(get_db)):
    n = ok = 0
    for r in db.query(BlockchainRecord).filter(BlockchainRecord.status.in_(["pending", "failed", "anchored"])).order_by(BlockchainRecord.id).limit(200):
        was = r.status
        r = chain.try_anchor(db, r, recheck=True)
        if was != "anchored" or r.status != "anchored" or getattr(r, "reanchored", False):   # only records that needed work
            n += 1
            ok += r.status == "anchored"
    return {"tried": n, "anchored": ok}
