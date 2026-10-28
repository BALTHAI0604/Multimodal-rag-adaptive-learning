"""Public, unauthenticated: badge share pages (token only; no private data unless the student opted in)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Badge, BadgeShare, Student, StudentBadge
from app.security.ratelimit import rate_limit

router = APIRouter(prefix="/public", tags=["Public"])


@router.get("/badge/{token}", dependencies=[Depends(rate_limit("badge", 60, 60))])
def badge(token: str, db: Session = Depends(get_db)):
    sh = db.query(BadgeShare).filter_by(token=token).first()
    if not sh or sh.revoked_at:
        raise HTTPException(404, "This share link is not available.")
    sb = db.query(StudentBadge).filter_by(id=sh.student_badge_id).first()
    b = db.query(Badge).filter_by(id=sb.badge_id).first()
    st = db.query(Student).filter_by(id=sb.student_id).first()
    return {"badge": {"name": b.name, "description": b.description, "icon": b.icon, "criteria": b.criteria},
            "awarded_at": sb.awarded_at.isoformat() + "Z", "holder": st.user.full_name if sh.include_name else "A student"}
