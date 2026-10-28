"""Audit logging helper.  Never put raw marks, transcripts or secrets in `details`."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.models import AuditLog, User


def audit(
    db: Session,
    action: str,
    resource_type: str,
    resource_id: Any = None,
    user: Optional[User] = None,
    request: Optional[Request] = None,
    details: Optional[Dict[str, Any]] = None,
    outcome: str = "success",
    commit: bool = True,
) -> AuditLog:
    entry = AuditLog(
        user_id=user.id if user else None,
        actor_role=user.role if user else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        ip_address=(request.client.host if request and request.client else None),
        request_id=getattr(getattr(request, "state", None), "request_id", None) if request else None,
        outcome=outcome,
        details_json=details or {},
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
