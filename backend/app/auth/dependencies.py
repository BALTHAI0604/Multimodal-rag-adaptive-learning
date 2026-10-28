"""FastAPI dependencies: authentication (Firebase / local) and role checks."""
from __future__ import annotations

import datetime
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.providers import AuthError, _ts, get_provider
from app.config import settings
from app.database import get_db
from app.models.models import User, utcnow

bearer = HTTPBearer(auto_error=False)


def _unauth(detail: str, code: str = "unauthenticated") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail,
                         headers={"WWW-Authenticate": "Bearer", "X-Auth-Error": code})


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise _unauth("Authentication required.")
    try:
        claims = get_provider().verify(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message,
                            headers={"WWW-Authenticate": "Bearer", "X-Auth-Error": exc.code})

    user = db.query(User).filter(User.firebase_uid == claims.uid).first()
    if user is None:
        # Valid identity but not provisioned by an administrator: never auto-create accounts.
        raise HTTPException(status_code=403, detail="This account has not been provisioned. Contact your administrator.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated.")
    if user.token_valid_after and claims.iat <= _ts(user.token_valid_after):
        raise _unauth("Session has been revoked. Please sign in again.", "token_revoked")
    if settings.AUTH_MODE == "firebase" and settings.REQUIRE_EMAIL_VERIFIED and not claims.email_verified:
        raise HTTPException(status_code=403, detail="E-mail address is not verified.")
    request.state.user_id = user.id
    return user


def require_roles(*roles: str) -> Callable:
    allowed = set(roles)

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="Your role does not permit this action.")
        return user

    return checker
