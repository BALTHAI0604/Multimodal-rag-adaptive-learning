import datetime
import secrets
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.auth.dependencies import bearer, get_current_user
from app.auth.providers import AuthError, LocalProvider, get_provider, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.models.models import Faculty, RefreshSession, Student, User, utcnow
from app.schemas.schemas import IdentifierRequest, LoginRequest
from app.security.audit import audit
from app.security.crypto import token_hash
from app.security.ratelimit import client_ip, limiter, rate_limit

router = APIRouter(prefix="/auth", tags=["Authentication"])
COOKIE = "rt"
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _set_cookie(resp: Response, token: Optional[str]) -> None:
    if token:
        resp.set_cookie(COOKIE, token, httponly=True, secure=settings.COOKIE_SECURE, samesite="strict",
                        max_age=settings.REFRESH_TOKEN_DAYS * 86400, path="/api/auth")


def _find_user(db: Session, identifier: str) -> Optional[User]:
    ident = identifier.strip()
    if "@" in ident:
        return db.query(User).filter(User.email == ident.lower()).first()
    st = db.query(Student).filter(Student.roll_number.ilike(ident)).first()
    if st:
        return db.query(User).filter(User.id == st.user_id).first()
    fac = db.query(Faculty).filter(Faculty.employee_id.ilike(ident)).first()
    return db.query(User).filter(User.id == fac.user_id).first() if fac else None


def user_payload(db: Session, user: User) -> dict:
    d = {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role,
         "email_verified": bool(user.email_verified)}
    if user.role == "student":
        s = db.query(Student).filter(Student.user_id == user.id).first()
        if s:
            from app.gamification.engine import effective_streak, level_for
            lv, _ = level_for(s.total_xp or 0)
            d.update({"student_id": s.id, "roll_number": s.roll_number, "department": s.department,
                      "class_name": s.class_name, "total_xp": s.total_xp, "level": lv[0], "level_title": lv[1],
                      "streak_days": effective_streak(s), "leaderboard_opt_in": bool(s.leaderboard_opt_in),
                      "display_alias": s.display_alias})
    elif user.role == "faculty":
        f = db.query(Faculty).filter(Faculty.user_id == user.id).first()
        if f:
            d.update({"faculty_id": f.id, "department": f.department, "employee_id": f.employee_id})
    return d


def _new_local_session(db: Session, user: User) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(RefreshSession(user_id=user.id, token_hash=token_hash(raw),
                          expires_at=utcnow() + datetime.timedelta(days=settings.REFRESH_TOKEN_DAYS)))
    db.commit()
    return raw


@router.get("/config")
def public_config():
    """Non-secret info the login screen needs."""
    return {"auth_mode": settings.AUTH_MODE, "environment": settings.ENVIRONMENT,
            "password_reset_sends_email": settings.AUTH_MODE == "firebase"}


@router.post("/login", dependencies=[Depends(rate_limit("login", 10, 60))])
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    limiter.check(f"login-id:{body.identifier.lower()}", 8, 300)
    provider = get_provider()
    generic = HTTPException(status_code=401, detail="Incorrect ID/e-mail or password.")
    user = _find_user(db, body.identifier)
    if user is None:
        verify_password(body.password, _DUMMY_HASH)           # equalise timing
        audit(db, "LOGIN_FAILED", "user", None, request=request, outcome="denied", details={"reason": "unknown"})
        raise generic
    if user.locked_until and user.locked_until > utcnow():
        audit(db, "LOGIN_LOCKED", "user", user.id, user=user, request=request, outcome="denied")
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
    if user.token_valid_after:      # tokens are stamped per second; never issue one in the revocation second
        from app.auth.providers import _ts
        import time
        wait = _ts(user.token_valid_after) + 1 - time.time()
        if 0 < wait <= 1.5:
            time.sleep(wait)
    try:
        if provider.name == "local":
            tokens = provider.sign_in(user, body.password)
        else:
            if not user.firebase_uid:
                raise AuthError("invalid_credentials", "x")
            tokens = provider.sign_in_email(user.email, body.password)
            if tokens.uid != user.firebase_uid:
                audit(db, "LOGIN_UID_MISMATCH", "user", user.id, user=user, request=request, outcome="denied")
                raise generic
            if settings.REQUIRE_EMAIL_VERIFIED and not tokens.email_verified:
                try:
                    provider.send_verification_email(tokens.id_token)
                except AuthError:
                    pass
                raise HTTPException(status_code=403, detail="Please verify your e-mail address. We have sent you a new verification link.",
                                    headers={"X-Auth-Error": "email_not_verified"})
    except AuthError as exc:
        if exc.code == "invalid_credentials":
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= settings.LOGIN_MAX_FAILURES:
                user.locked_until = utcnow() + datetime.timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
                user.failed_login_count = 0
            db.commit()
            audit(db, "LOGIN_FAILED", "user", user.id, user=user, request=request, outcome="denied")
            raise generic
        raise HTTPException(status_code=exc.status, detail=exc.message)
    if not user.is_active:
        audit(db, "LOGIN_DEACTIVATED", "user", user.id, user=user, request=request, outcome="denied")
        raise HTTPException(status_code=403, detail="This account has been deactivated. Contact your administrator.")
    user.failed_login_count, user.locked_until, user.email_verified = 0, None, True
    db.commit()
    audit(db, "LOGIN_SUCCESS", "user", user.id, user=user, request=request)
    _set_cookie(response, tokens.refresh_token or _new_local_session(db, user))
    return {"access_token": tokens.id_token, "expires_in": tokens.expires_in, "user": user_payload(db, user)}


@router.post("/refresh", dependencies=[Depends(rate_limit("refresh", 60, 60))])
def refresh(request: Request, response: Response, rt: Optional[str] = Cookie(default=None), db: Session = Depends(get_db)):
    if not rt:
        raise HTTPException(status_code=401, detail="No session.")
    provider = get_provider()
    try:
        if provider.name == "local":
            sess = db.query(RefreshSession).filter_by(token_hash=token_hash(rt)).first()
            if not sess or sess.revoked or sess.expires_at < utcnow():
                raise AuthError("invalid_token", "Session expired. Please sign in again.")
            user = db.query(User).filter_by(id=sess.user_id).first()
            if not user or not user.is_active:
                raise AuthError("invalid_token", "Session expired. Please sign in again.")
            sess.revoked = True
            db.commit()
            new_rt, access = _new_local_session(db, user), provider.mint_for(user)
            expires = settings.ACCESS_TOKEN_MINUTES * 60
        else:
            tokens = provider.refresh(rt)
            user = db.query(User).filter_by(firebase_uid=tokens.uid).first()
            if not user or not user.is_active:
                raise AuthError("invalid_token", "Session expired. Please sign in again.")
            claims = provider.verify(tokens.id_token)
            from app.auth.providers import _ts
            if user.token_valid_after and claims.iat <= _ts(user.token_valid_after):
                raise AuthError("token_revoked", "Session revoked. Please sign in again.")
            new_rt, access, expires = tokens.refresh_token, tokens.id_token, tokens.expires_in
    except AuthError as exc:
        response.delete_cookie(COOKIE, path="/api/auth")
        raise HTTPException(status_code=exc.status, detail=exc.message)
    _set_cookie(response, new_rt)
    return {"access_token": access, "expires_in": expires, "user": user_payload(db, user)}


@router.post("/logout")
def logout(request: Request, response: Response, rt: Optional[str] = Cookie(default=None),
           db: Session = Depends(get_db)):
    """Revokes the session.  Works even if the access token has already expired."""
    provider = get_provider()
    user = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            claims = provider.verify(auth[7:])
            user = db.query(User).filter_by(firebase_uid=claims.uid).first()
        except AuthError:
            user = None
    if rt and provider.name == "local":
        s = db.query(RefreshSession).filter_by(token_hash=token_hash(rt)).first()
        if s:
            s.revoked = True
            user = user or db.query(User).filter_by(id=s.user_id).first()
    if user:
        user.token_valid_after = utcnow()
        db.query(RefreshSession).filter_by(user_id=user.id, revoked=False).update({"revoked": True})
        db.commit()
        try:
            provider.revoke(user.firebase_uid)
        except Exception:
            pass
        audit(db, "LOGOUT", "user", user.id, user=user, request=request)
    response.delete_cookie(COOKIE, path="/api/auth")
    return {"status": "signed_out"}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return user_payload(db, user)


@router.post("/password-reset", dependencies=[Depends(rate_limit("pwreset", 5, 300))])
def password_reset(body: IdentifierRequest, request: Request, db: Session = Depends(get_db)):
    """Always answers the same way so it cannot be used to discover which accounts exist."""
    user = _find_user(db, body.identifier)
    if user and user.is_active:
        get_provider().send_password_reset(user.email)
        audit(db, "PASSWORD_RESET_REQUESTED", "user", user.id, user=user, request=request)
    msg = ("If the account exists, a reset link has been e-mailed." if settings.AUTH_MODE == "firebase" else
           "Local development mode sends no e-mail. Ask an administrator to reset your password.")
    return {"status": "ok", "message": msg}
