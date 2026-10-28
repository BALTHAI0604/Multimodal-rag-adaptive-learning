"""Field-level encryption (Fernet / AES-128-CBC + HMAC) and keyed hashing."""
from __future__ import annotations

import hashlib
import hmac
import json
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import Text, TypeDecorator

from app.config import settings

_PREFIX = "enc1:"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(settings.fernet_key())


def encrypt_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not value.startswith(_PREFIX):          # legacy plaintext row
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        return "[unreadable: encryption key changed]"


class EncryptedText(TypeDecorator):
    """Transparently encrypts text columns at rest (transcripts, AI chats, audit snapshots)."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_str(value)

    def process_result_value(self, value, dialect):
        return decrypt_str(value)


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def keyed_hash(obj) -> str:
    """HMAC-SHA256 of the canonical JSON.  A plain SHA-256 of marks is brute-forceable (tiny input
    space), so the on-chain value is keyed with a server-side secret."""
    return hmac.new(settings.hmac_key(), canonical_json(obj).encode("utf-8"), hashlib.sha256).hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
