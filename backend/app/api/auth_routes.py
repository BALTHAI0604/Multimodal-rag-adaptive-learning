import datetime
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.providers import (
    AuthError,
    get_provider,
    hash_password,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.models.models import (
    Faculty,
    RefreshSession,
    Student,
    User,
    utcnow,
)
from app.schemas.schemas import IdentifierRequest, LoginRequest
from app.security.audit import audit
from app.security.crypto import token_hash
from app.security.ratelimit import limiter, rate_limit


router = APIRouter(prefix="/auth", tags=["Authentication"])

COOKIE = "rt"

_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _set_cookie(resp: Response, token: Optional[str]) -> None:
    if token:
        resp.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=settings.COOKIE_SECURE,
            samesite="strict",
            max_age=settings.REFRESH_TOKEN_DAYS * 86400,
            path="/api/auth",
        )


def _find_user(db: Session, identifier: str) -> Optional[User]:
    ident = identifier.strip()

    if "@" in ident:
        return (
            db.query(User)
            .filter(User.email == ident.lower())
            .first()
        )

    student = (
        db.query(Student)
        .filter(Student.roll_number.ilike(ident))
        .first()
    )

    if student:
        return (
            db.query(User)
            .filter(User.id == student.user_id)
            .first()
        )

    faculty = (
        db.query(Faculty)
        .filter(Faculty.employee_id.ilike(ident))
        .first()
    )

    if faculty:
        return (
            db.query(User)
            .filter(User.id == faculty.user_id)
            .first()
        )

    return None


def user_payload(db: Session, user: User) -> dict:
    data = {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "email_verified": bool(user.email_verified),
    }

    if user.role == "student":
        student = (
            db.query(Student)
            .filter(Student.user_id == user.id)
            .first()
        )

        if student:
            from app.gamification.engine import (
                effective_streak,
                level_for,
            )

            level, _ = level_for(student.total_xp or 0)

            data.update(
                {
                    "student_id": student.id,
                    "roll_number": student.roll_number,
                    "department": student.department,
                    "class_name": student.class_name,
                    "total_xp": student.total_xp,
                    "level": level[0],
                    "level_title": level[1],
                    "streak_days": effective_streak(student),
                    "leaderboard_opt_in": bool(
                        student.leaderboard_opt_in
                    ),
                    "display_alias": student.display_alias,
                }
            )

    elif user.role == "faculty":
        faculty = (
            db.query(Faculty)
            .filter(Faculty.user_id == user.id)
            .first()
        )

        if faculty:
            data.update(
                {
                    "faculty_id": faculty.id,
                    "department": faculty.department,
                    "employee_id": faculty.employee_id,
                }
            )

    return data


def _new_local_session(db: Session, user: User) -> str:
    raw_token = secrets.token_urlsafe(32)

    session = RefreshSession(
        user_id=user.id,
        token_hash=token_hash(raw_token),
        expires_at=utcnow()
        + datetime.timedelta(
            days=settings.REFRESH_TOKEN_DAYS
        ),
    )

    db.add(session)
    db.commit()

    return raw_token


@router.get("/config")
def public_config():
    """Return non-secret authentication configuration."""

    return {
        "auth_mode": settings.AUTH_MODE,
        "environment": settings.ENVIRONMENT,
        "password_reset_sends_email": (
            settings.AUTH_MODE == "firebase"
        ),
    }


@router.post(
    "/login",
    dependencies=[Depends(rate_limit("login", 10, 60))],
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    limiter.check(
        f"login-id:{body.identifier.lower()}",
        8,
        300,
    )

    provider = get_provider()

    generic_error = HTTPException(
        status_code=401,
        detail="Incorrect ID/e-mail or password.",
    )

    user = _find_user(db, body.identifier)

    if user is None:
        verify_password(body.password, _DUMMY_HASH)

        audit(
            db,
            "LOGIN_FAILED",
            "user",
            None,
            request=request,
            outcome="denied",
            details={"reason": "unknown"},
        )

        raise generic_error

    if user.locked_until and user.locked_until > utcnow():
        audit(
            db,
            "LOGIN_LOCKED",
            "user",
            user.id,
            user=user,
            request=request,
            outcome="denied",
        )

        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Try again later.",
        )

    if user.token_valid_after:
        from app.auth.providers import _ts

        wait = _ts(user.token_valid_after) + 1 - time.time()

        if 0 < wait <= 1.5:
            time.sleep(wait)

    try:
        if provider.name == "local":
            tokens = provider.sign_in(
                user,
                body.password,
            )

        else:
            if not user.firebase_uid:
                raise AuthError(
                    "invalid_credentials",
                    "Invalid credentials.",
                )

            tokens = provider.sign_in_email(
                user.email,
                body.password,
            )

            if tokens.uid != user.firebase_uid:
                audit(
                    db,
                    "LOGIN_UID_MISMATCH",
                    "user",
                    user.id,
                    user=user,
                    request=request,
                    outcome="denied",
                )

                raise generic_error

            if (
                settings.REQUIRE_EMAIL_VERIFIED
                and not tokens.email_verified
            ):
                try:
                    provider.send_verification_email(
                        tokens.id_token
                    )
                except AuthError:
                    pass

                raise HTTPException(
                    status_code=403,
                    detail=(
                        "Please verify your e-mail address. "
                        "We have sent you a new verification link."
                    ),
                    headers={
                        "X-Auth-Error": "email_not_verified"
                    },
                )

    except AuthError as exc:
        if exc.code == "invalid_credentials":
            user.failed_login_count = (
                user.failed_login_count or 0
            ) + 1

            if (
                user.failed_login_count
                >= settings.LOGIN_MAX_FAILURES
            ):
                user.locked_until = (
                    utcnow()
                    + datetime.timedelta(
                        minutes=settings.LOGIN_LOCK_MINUTES
                    )
                )

                user.failed_login_count = 0

            db.commit()

            audit(
                db,
                "LOGIN_FAILED",
                "user",
                user.id,
                user=user,
                request=request,
                outcome="denied",
            )

            raise generic_error

        raise HTTPException(
            status_code=exc.status,
            detail=exc.message,
        )

    if not user.is_active:
        audit(
            db,
            "LOGIN_DEACTIVATED",
            "user",
            user.id,
            user=user,
            request=request,
            outcome="denied",
        )

        raise HTTPException(
            status_code=403,
            detail=(
                "This account has been deactivated. "
                "Contact your administrator."
            ),
        )

    user.failed_login_count = 0
    user.locked_until = None

    if provider.name == "firebase":
        user.email_verified = bool(tokens.email_verified)
    else:
        user.email_verified = True

    db.commit()

    audit(
        db,
        "LOGIN_SUCCESS",
        "user",
        user.id,
        user=user,
        request=request,
    )

    refresh_token = tokens.refresh_token

    if not refresh_token:
        refresh_token = _new_local_session(db, user)

    _set_cookie(response, refresh_token)

    return {
        "access_token": tokens.id_token,
        "expires_in": tokens.expires_in,
        "user": user_payload(db, user),
    }


@router.post(
    "/refresh",
    dependencies=[Depends(rate_limit("refresh", 60, 60))],
)
def refresh(
    request: Request,
    response: Response,
    rt: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not rt:
        raise HTTPException(
            status_code=401,
            detail="No session.",
        )

    provider = get_provider()

    try:
        if provider.name == "local":
            session = (
                db.query(RefreshSession)
                .filter_by(token_hash=token_hash(rt))
                .first()
            )

            if (
                not session
                or session.revoked
                or session.expires_at < utcnow()
            ):
                raise AuthError(
                    "invalid_token",
                    "Session expired. Please sign in again.",
                )

            user = (
                db.query(User)
                .filter_by(id=session.user_id)
                .first()
            )

            if not user or not user.is_active:
                raise AuthError(
                    "invalid_token",
                    "Session expired. Please sign in again.",
                )

            session.revoked = True
            db.commit()

            new_refresh_token = _new_local_session(
                db,
                user,
            )

            access_token = provider.mint_for(user)

            expires = settings.ACCESS_TOKEN_MINUTES * 60

        else:
            tokens = provider.refresh(rt)

            user = (
                db.query(User)
                .filter_by(firebase_uid=tokens.uid)
                .first()
            )

            if not user or not user.is_active:
                raise AuthError(
                    "invalid_token",
                    "Session expired. Please sign in again.",
                )

            claims = provider.verify(tokens.id_token)

            from app.auth.providers import _ts

            if (
                user.token_valid_after
                and claims.iat <= _ts(user.token_valid_after)
            ):
                raise AuthError(
                    "token_revoked",
                    "Session revoked. Please sign in again.",
                )

            new_refresh_token = tokens.refresh_token
            access_token = tokens.id_token
            expires = tokens.expires_in

    except AuthError as exc:
        response.delete_cookie(
            COOKIE,
            path="/api/auth",
        )

        raise HTTPException(
            status_code=exc.status,
            detail=exc.message,
        )

    _set_cookie(
        response,
        new_refresh_token,
    )

    return {
        "access_token": access_token,
        "expires_in": expires,
        "user": user_payload(db, user),
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    rt: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    """Revoke the current session."""

    provider = get_provider()
    user = None

    authorization = request.headers.get(
        "authorization",
        "",
    )

    if authorization.lower().startswith("bearer "):
        try:
            claims = provider.verify(
                authorization[7:]
            )

            user = (
                db.query(User)
                .filter_by(firebase_uid=claims.uid)
                .first()
            )

        except AuthError:
            user = None

    if rt and provider.name == "local":
        session = (
            db.query(RefreshSession)
            .filter_by(token_hash=token_hash(rt))
            .first()
        )

        if session:
            session.revoked = True

            if user is None:
                user = (
                    db.query(User)
                    .filter_by(id=session.user_id)
                    .first()
                )

    if user:
        user.token_valid_after = utcnow()

        (
            db.query(RefreshSession)
            .filter_by(
                user_id=user.id,
                revoked=False,
            )
            .update({"revoked": True})
        )

        db.commit()

        try:
            provider.revoke(user.firebase_uid)
        except Exception:
            pass

        audit(
            db,
            "LOGOUT",
            "user",
            user.id,
            user=user,
            request=request,
        )

    response.delete_cookie(
        COOKIE,
        path="/api/auth",
    )

    return {"status": "signed_out"}


@router.get("/me")
def me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return user_payload(db, user)


@router.post(
    "/password-reset",
    dependencies=[Depends(rate_limit("pwreset", 5, 300))],
)
def password_reset(
    body: IdentifierRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return a generic response for password-reset requests."""

    user = _find_user(
        db,
        body.identifier,
    )

    if user and user.is_active:
        get_provider().send_password_reset(user.email)

        audit(
            db,
            "PASSWORD_RESET_REQUESTED",
            "user",
            user.id,
            user=user,
            request=request,
        )

    if settings.AUTH_MODE == "firebase":
        message = (
            "If the account exists, a reset link has been e-mailed."
        )
    else:
        message = (
            "Local development mode sends no e-mail. "
            "Ask an administrator to reset your password."
        )

    return {
        "status": "ok",
        "message": message,
    }