"""Optional mirror of {role, active, displayName} into Firestore users/{uid}.

The authoritative academic data lives in the SQL database.  The mirror exists so that
Firestore Security Rules (see ../../firestore.rules) can protect it and so a Firebase-only
client could read its own role.  The server NEVER trusts the mirror for authorization.
"""
import logging

from app.config import settings

log = logging.getLogger("firestore_sync")


def sync_user(user) -> None:
    if not (settings.FIRESTORE_SYNC and settings.AUTH_MODE == "firebase" and user.firebase_uid):
        return
    try:
        from firebase_admin import firestore
        firestore.client().collection("users").document(user.firebase_uid).set(
            {"role": user.role, "active": bool(user.is_active), "displayName": user.full_name,
             "updatedAt": firestore.SERVER_TIMESTAMP}, merge=True)
    except Exception as exc:  # never break the request because the mirror failed
        log.warning("Firestore sync failed: %s", type(exc).__name__)
