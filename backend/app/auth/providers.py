"""Identity providers.

* FirebaseProvider  - real Firebase Authentication (production path).  ID tokens are verified with
  the Firebase Admin SDK; password sign-in, refresh, e-mail verification and password-reset use the
  Identity Toolkit REST API from the *server*, so the browser never needs Firebase SDK keys and
  student e-mails are never exposed for "student-ID login".
* LocalProvider     - development / test only (AUTH_MODE=local).  Bcrypt passwords and locally
  signed JWTs.  It is refused when ENVIRONMENT=production.

Both providers expose the same small interface so the rest of the code does not care.
"""
from __future__ import annotations

import datetime
import logging
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import bcrypt
import httpx
import jwt

from app.config import settings

log = logging.getLogger("auth")

COMMON_PASSWORDS = {
    "password", "password1", "password123", "123456789", "1234567890", "qwertyuiop", "iloveyou1",
    "admin12345", "welcome123", "letmein123", "changeme123",
}


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 401):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass
class Claims:
    uid: str
    email: Optional[str]
    email_verified: bool
    iat: int
    exp: int


@dataclass
class Tokens:
    id_token: str
    refresh_token: Optional[str]
    expires_in: int
    uid: str
    email_verified: bool


def validate_password_strength(pw: str) -> None:
    if len(pw) < 10:
        raise AuthError("weak_password", "Password must be at least 10 characters long.", 422)
    if pw.lower() in COMMON_PASSWORDS or re.fullmatch(r"(.)\1+", pw):
        raise AuthError("weak_password", "That password is too common. Choose a different one.", 422)
    classes = sum(bool(re.search(p, pw)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w\s]"))
    if classes < 3:
        raise AuthError("weak_password", "Use at least three of: lowercase, uppercase, digits, symbols.", 422)


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: Optional[str]) -> bool:
    if not hashed or not hashed.startswith("$2"):
        return False
    try:
        return bcrypt.checkpw(pw.encode("utf-8")[:72], hashed.encode())
    except ValueError:
        return False


def _ts(dt: datetime.datetime) -> int:
    return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())


# ======================================================================================
# Local (development/test) provider
# ======================================================================================
class LocalProvider:
    name = "local"
    ISS = "local-dev"
    AUD = "adaptive-learning-local"

    def __init__(self) -> None:
        if settings.is_production:
            raise RuntimeError("LocalProvider must never run in production")

    # ---- tokens ----
    def _issue(self, uid: str, email: str, ttl_min: Optional[int] = None) -> str:
        now = int(time.time())
        ttl = (ttl_min or settings.ACCESS_TOKEN_MINUTES) * 60
        return jwt.encode(
            {"iss": self.ISS, "aud": self.AUD, "sub": uid, "email": email, "email_verified": True,
             "iat": now, "exp": now + ttl},
            settings.local_auth_secret(), algorithm="HS256",
        )

    def verify(self, token: str) -> Claims:
        try:
            p = jwt.decode(token, settings.local_auth_secret(), algorithms=["HS256"], audience=self.AUD,
                           issuer=self.ISS, options={"require": ["exp", "iat", "sub"]})
        except jwt.ExpiredSignatureError:
            raise AuthError("token_expired", "Session expired. Please sign in again.")
        except jwt.PyJWTError:
            raise AuthError("invalid_token", "Invalid authentication token.")
        return Claims(uid=p["sub"], email=p.get("email"), email_verified=True, iat=int(p["iat"]), exp=int(p["exp"]))

    # ---- account lifecycle ----
    def create_account(self, email: str, display_name: str, password: Optional[str]) -> dict:
        """Returns {uid, password_hash, temp_password?}.  Local accounts get a one-time temp password."""
        temp = None
        if password is None:
            temp = secrets.token_urlsafe(12) + "Aa1!"
            password = temp
        else:
            validate_password_strength(password)
        return {"uid": f"local-{uuid.uuid4().hex}", "password_hash": hash_password(password), "temp_password": temp}

    def set_disabled(self, uid: str, disabled: bool) -> None:
        return None

    def revoke(self, uid: str) -> None:
        return None

    def send_password_reset(self, email: str) -> None:
        log.info("local mode: password reset requested (no e-mail is sent in local mode)")

    def delete_account(self, uid: str) -> None:
        return None

    def sign_in(self, user, password: str) -> Tokens:
        """`user` is the DB row (local mode keeps credentials in the DB)."""
        if not verify_password(password, user.hashed_password):
            raise AuthError("invalid_credentials", "Incorrect ID/e-mail or password.")
        return Tokens(self._issue(user.firebase_uid, user.email), None, settings.ACCESS_TOKEN_MINUTES * 60,
                      user.firebase_uid, True)

    def mint_for(self, user) -> str:
        return self._issue(user.firebase_uid, user.email)


# ======================================================================================
# Firebase provider
# ======================================================================================
class FirebaseProvider:
    name = "firebase"

    def __init__(self) -> None:
        import firebase_admin
        from firebase_admin import credentials

        if settings.FIREBASE_AUTH_EMULATOR_HOST:
            os.environ["FIREBASE_AUTH_EMULATOR_HOST"] = settings.FIREBASE_AUTH_EMULATOR_HOST
        self._emulator = settings.FIREBASE_AUTH_EMULATOR_HOST or ""
        if not firebase_admin._apps:
            opts = {"projectId": settings.FIREBASE_PROJECT_ID} if settings.FIREBASE_PROJECT_ID else None
            if self._emulator:
                firebase_admin.initialize_app(credentials.AnonymousCredentials(), opts)
            elif settings.GOOGLE_APPLICATION_CREDENTIALS:
                firebase_admin.initialize_app(credentials.Certificate(settings.GOOGLE_APPLICATION_CREDENTIALS), opts)
            else:
                firebase_admin.initialize_app(credentials.ApplicationDefault(), opts)

    # ---- REST helpers ----
    def _idt(self, path: str) -> str:
        key = settings.FIREBASE_WEB_API_KEY or "emulator-key"
        if self._emulator:
            return f"http://{self._emulator}/identitytoolkit.googleapis.com/v1/{path}?key={key}"
        return f"https://identitytoolkit.googleapis.com/v1/{path}?key={key}"

    def _securetoken(self) -> str:
        key = settings.FIREBASE_WEB_API_KEY or "emulator-key"
        if self._emulator:
            return f"http://{self._emulator}/securetoken.googleapis.com/v1/token?key={key}"
        return f"https://securetoken.googleapis.com/v1/token?key={key}"

    def _post(self, url: str, payload: dict) -> dict:
        try:
            r = httpx.post(url, json=payload, timeout=15)
        except httpx.HTTPError as exc:
            raise AuthError("auth_unavailable", "Authentication service is unreachable.", 503) from exc
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            msg = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
            if any(m in msg for m in ("INVALID_PASSWORD", "EMAIL_NOT_FOUND", "INVALID_LOGIN_CREDENTIALS")):
                raise AuthError("invalid_credentials", "Incorrect ID/e-mail or password.")
            if "USER_DISABLED" in msg:
                raise AuthError("account_disabled", "This account has been deactivated.", 403)
            if "TOO_MANY_ATTEMPTS" in msg:
                raise AuthError("too_many_attempts", "Too many attempts. Try again later.", 429)
            if any(m in msg for m in ("TOKEN_EXPIRED", "INVALID_REFRESH_TOKEN", "USER_NOT_FOUND")):
                raise AuthError("invalid_token", "Session expired. Please sign in again.")
            log.warning("firebase REST error: %s", msg)
            raise AuthError("auth_error", "Authentication failed.", 400)
        return data

    # ---- sign-in ----
    def sign_in_email(self, email: str, password: str) -> Tokens:
        d = self._post(self._idt("accounts:signInWithPassword"),
                       {"email": email, "password": password, "returnSecureToken": True})
        claims = self.verify(d["idToken"])
        return Tokens(d["idToken"], d.get("refreshToken"), int(d.get("expiresIn", 3600)), d["localId"],
                      claims.email_verified)

    def refresh(self, refresh_token: str) -> Tokens:
        d = self._post(self._securetoken(), {"grant_type": "refresh_token", "refresh_token": refresh_token})
        claims = self.verify(d["id_token"])
        return Tokens(d["id_token"], d.get("refresh_token"), int(d.get("expires_in", 3600)), d["user_id"],
                      claims.email_verified)

    def send_verification_email(self, id_token: str) -> None:
        self._post(self._idt("accounts:sendOobCode"), {"requestType": "VERIFY_EMAIL", "idToken": id_token})

    def send_password_reset(self, email: str) -> None:
        try:
            self._post(self._idt("accounts:sendOobCode"), {"requestType": "PASSWORD_RESET", "email": email})
        except AuthError as exc:  # never reveal whether the e-mail exists
            log.info("password reset request ignored: %s", exc.code)

    # ---- token verification ----
    def verify(self, token: str) -> Claims:
        from firebase_admin import auth as fb_auth
        try:
            d = fb_auth.verify_id_token(token, check_revoked=False)
        except fb_auth.ExpiredIdTokenError:
            raise AuthError("token_expired", "Session expired. Please sign in again.")
        except (fb_auth.InvalidIdTokenError, fb_auth.RevokedIdTokenError, ValueError):
            raise AuthError("invalid_token", "Invalid authentication token.")
        except fb_auth.CertificateFetchError as exc:
            raise AuthError("auth_unavailable", "Authentication service is unreachable.", 503) from exc
        except Exception as exc:  # defensive: any other SDK failure is an auth failure, never a 500
            log.warning("verify_id_token unexpected error: %s", type(exc).__name__)
            raise AuthError("invalid_token", "Invalid authentication token.")
        return Claims(uid=d["uid"], email=d.get("email"), email_verified=bool(d.get("email_verified")),
                      iat=int(d.get("iat", 0)), exp=int(d.get("exp", 0)))

    # ---- account lifecycle (admin) ----
    def create_account(self, email: str, display_name: str, password: Optional[str]) -> dict:
        from firebase_admin import auth as fb_auth
        pw = password or (secrets.token_urlsafe(24) + "Aa1!")
        if password:
            validate_password_strength(password)
        try:
            rec = fb_auth.create_user(email=email, display_name=display_name, password=pw, email_verified=False)
        except fb_auth.EmailAlreadyExistsError:
            raise AuthError("email_exists", "An account with that e-mail already exists.", 409)
        except ValueError as exc:
            raise AuthError("invalid_account", str(exc), 422)
        return {"uid": rec.uid, "password_hash": None, "temp_password": None}

    def set_disabled(self, uid: str, disabled: bool) -> None:
        from firebase_admin import auth as fb_auth
        fb_auth.update_user(uid, disabled=disabled)

    def revoke(self, uid: str) -> None:
        from firebase_admin import auth as fb_auth
        fb_auth.revoke_refresh_tokens(uid)

    def delete_account(self, uid: str) -> None:
        from firebase_admin import auth as fb_auth
        try:
            fb_auth.delete_user(uid)
        except Exception:  # pragma: no cover
            pass


_provider = None


def get_provider():
    global _provider
    if _provider is None:
        _provider = FirebaseProvider() if settings.AUTH_MODE == "firebase" else LocalProvider()
    return _provider


def set_provider_for_tests(p) -> None:
    global _provider
    _provider = p
