"""Central configuration.

All secrets come from environment variables (or a local .env file that is git-ignored).
Nothing in this file is a real credential.  See ../.env.example for every setting.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from pathlib import Path
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
PROJECT_ROOT = BASE_DIR.parent                              # repository root


# Well-known Hardhat/Anvil development accounts (private keys are public). Never valid outside a local test chain.
HARDHAT_PUBLIC_KEYS = {
    "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdb9bd4a",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[str(PROJECT_ROOT / ".env"), str(BASE_DIR / ".env")],
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PROJECT_NAME: str = "Multimodal RAG-Driven Adaptive Learning"
    VERSION: str = "2.0.0"
    API_V1_STR: str = "/api"

    # development | test | production
    ENVIRONMENT: str = "development"

    # ---------------- paths ----------------
    DATA_DIR: Path = PROJECT_ROOT / "data"
    UPLOAD_DIR: Path = PROJECT_ROOT / "uploads"
    DATABASE_URL: str = ""

    # ---------------- authentication ----------------
    # "firebase": real Firebase Authentication (required in production)
    # "local":    development/test only.  Bcrypt passwords + locally signed tokens.
    AUTH_MODE: str = "local"
    FIREBASE_PROJECT_ID: str = ""
    FIREBASE_WEB_API_KEY: str = ""           # public web API key (not a secret) - used server-side for password sign-in
    GOOGLE_APPLICATION_CREDENTIALS: str = "" # path to service-account JSON, never committed
    FIREBASE_AUTH_EMULATOR_HOST: str = ""    # e.g. 127.0.0.1:9099 for the free local emulator
    FIRESTORE_SYNC: bool = False             # mirror {role, active} of each user into Firestore users/{uid}
    REQUIRE_EMAIL_VERIFIED: bool = True
    LOCAL_AUTH_SECRET: str = ""              # dev only; auto-generated into data/.local_auth_secret if empty
    ACCESS_TOKEN_MINUTES: int = 60
    REFRESH_TOKEN_DAYS: int = 14
    COOKIE_SECURE: bool = False              # set true behind HTTPS
    LOGIN_MAX_FAILURES: int = 8
    LOGIN_LOCK_MINUTES: int = 15

    # ---------------- web / security ----------------
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    SERVE_FRONTEND: bool = True              # serve frontend/dist from the API when it has been built
    FRONTEND_DIST: Path = PROJECT_ROOT / "frontend" / "dist"
    RATE_LIMIT_ENABLED: bool = True
    MAX_UPLOAD_MB: int = 25
    MAX_MEDIA_UPLOAD_MB: int = 200
    MAX_AUDIO_ANSWER_MB: int = 15

    # ---------------- crypto ----------------
    # Fernet key (urlsafe base64, 32 bytes).  Generate:  python -m app.cli gen-keys
    DATA_ENCRYPTION_KEY: str = ""
    BLOCKCHAIN_HMAC_KEY: str = ""            # defaults to a key derived from DATA_ENCRYPTION_KEY

    # ---------------- AI / RAG ----------------
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"
    OLLAMA_TIMEOUT_SEC: int = 180
    # auto | sentence_transformers | hashing
    EMBEDDING_BACKEND: str = "auto"
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-small"
    RAG_MIN_SCORE: float = -1.0              # -1 => backend specific default
    RAG_TOP_K: int = 4
    CHUNK_SIZE: int = 700
    CHUNK_OVERLAP: int = 120
    OCR_ENABLED: bool = True
    OCR_LANGS: str = "eng"                   # e.g. "eng+tam+hin" if those tessdata files are installed

    # ---------------- speech ----------------
    WHISPER_MODEL: str = "small"             # tiny|base|small|medium|large-v3 (faster-whisper)
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"
    PIPER_BIN: str = "piper"
    PIPER_VOICES_DIR: str = ""               # folder with *.onnx voices
    PIPER_VOICE_EN: str = ""                 # file names inside PIPER_VOICES_DIR
    PIPER_VOICE_HI: str = ""
    PIPER_VOICE_TA: str = ""
    PIPER_VOICE_KN: str = ""
    PIPER_VOICE_ML: str = ""

    # ---------------- privacy / retention ----------------
    VOICE_STORE_AUDIO: bool = False          # keep raw answer recordings? default: delete after transcription
    VOICE_AUDIO_RETENTION_DAYS: int = 7
    CONVERSATION_RETENTION_DAYS: int = 90
    ASSESSMENT_GRACE_SECONDS: int = 20
    APP_TIMEZONE: str = "Asia/Kolkata"

    # ---------------- blockchain ----------------
    BLOCKCHAIN_ENABLED: bool = True
    BLOCKCHAIN_RPC_URL: str = "http://127.0.0.1:8545"
    CONTRACT_ADDRESS: str = ""
    BLOCKCHAIN_PRIVATE_KEY: str = ""         # registrar key. Empty => use the node's unlocked account #0 (local dev only)

    @field_validator("ENVIRONMENT")
    @classmethod
    def _env(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"development", "test", "production"}:
            raise ValueError("ENVIRONMENT must be development, test or production")
        return v

    @field_validator("AUTH_MODE")
    @classmethod
    def _auth_mode(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"firebase", "local"}:
            raise ValueError("AUTH_MODE must be 'firebase' or 'local'")
        return v

    # ----------- derived helpers -----------
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip() and o.strip() != "*"]

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        name = "test.db" if self.ENVIRONMENT == "test" else "app.db"
        return f"sqlite:///{(self.DATA_DIR / name).as_posix()}"

    def fernet_key(self) -> bytes:
        """Return the symmetric data-encryption key.

        In development a key is generated once and stored in data/.dev_encryption_key so that
        data survives restarts.  In production the key MUST come from the environment.
        """
        if self.DATA_ENCRYPTION_KEY:
            return self.DATA_ENCRYPTION_KEY.encode()
        path = self.DATA_DIR / ".dev_encryption_key"
        if path.exists():
            return path.read_text().strip().encode()
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key

    def hmac_key(self) -> bytes:
        if self.BLOCKCHAIN_HMAC_KEY:
            return self.BLOCKCHAIN_HMAC_KEY.encode()
        return hashlib.sha256(b"blockchain-hmac|" + self.fernet_key()).digest()

    def local_auth_secret(self) -> str:
        if self.LOCAL_AUTH_SECRET:
            return self.LOCAL_AUTH_SECRET
        path = self.DATA_DIR / ".local_auth_secret"
        if path.exists():
            return path.read_text().strip()
        secret = secrets.token_urlsafe(48)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return secret

    def validate_for_startup(self) -> None:
        """Refuse to start in production with insecure settings."""
        problems = []
        if self.is_production:
            if self.AUTH_MODE != "firebase":
                problems.append("AUTH_MODE must be 'firebase' in production (local auth is development-only).")
            if not self.FIREBASE_PROJECT_ID:
                problems.append("FIREBASE_PROJECT_ID is required in production.")
            if not self.FIREBASE_WEB_API_KEY:
                problems.append("FIREBASE_WEB_API_KEY is required for password sign-in in production.")
            if not self.DATA_ENCRYPTION_KEY:
                problems.append("DATA_ENCRYPTION_KEY must be set in production.")
            if not self.cors_origin_list:
                problems.append("CORS_ORIGINS must list your real front-end origin(s).")
            if not self.COOKIE_SECURE:
                problems.append("COOKIE_SECURE must be true in production (serve over HTTPS).")
            if self.FIREBASE_AUTH_EMULATOR_HOST:
                problems.append("FIREBASE_AUTH_EMULATOR_HOST must not be set in production.")
        # ---- checks that apply in every environment: fail fast with a readable message instead of a crash later
        if self.DATA_ENCRYPTION_KEY:
            try:
                from cryptography.fernet import Fernet
                Fernet(self.DATA_ENCRYPTION_KEY.encode())
            except Exception:
                problems.append("DATA_ENCRYPTION_KEY is not a valid Fernet key. Generate one: python -m app.cli gen-keys")
        if self.BLOCKCHAIN_HMAC_KEY and len(self.BLOCKCHAIN_HMAC_KEY) < 32:
            problems.append("BLOCKCHAIN_HMAC_KEY is too short (need >= 32 characters). Generate one: python -m app.cli gen-keys")
        if self.CONTRACT_ADDRESS and not re.fullmatch(r"0x[0-9a-fA-F]{40}", self.CONTRACT_ADDRESS):
            problems.append("CONTRACT_ADDRESS must look like 0x followed by 40 hex characters.")
        if self.BLOCKCHAIN_ENABLED and not re.match(r"https?://", self.BLOCKCHAIN_RPC_URL):
            problems.append("BLOCKCHAIN_RPC_URL must start with http:// or https://.")
        if self.BLOCKCHAIN_PRIVATE_KEY and not re.fullmatch(r"(0x)?[0-9a-fA-F]{64}", self.BLOCKCHAIN_PRIVATE_KEY):
            problems.append("BLOCKCHAIN_PRIVATE_KEY must be 64 hex characters (never commit it).")
        if self.is_production and self.BLOCKCHAIN_ENABLED:
            if self.BLOCKCHAIN_PRIVATE_KEY.lower().removeprefix("0x") in HARDHAT_PUBLIC_KEYS:
                problems.append("BLOCKCHAIN_PRIVATE_KEY is a publicly known Hardhat development key; never use it in production.")
            if re.match(r"https?://(localhost|127\.0\.0\.1|\[?::1)", self.BLOCKCHAIN_RPC_URL):
                problems.append("BLOCKCHAIN_RPC_URL points at a local node (localhost); a local Hardhat chain is not a production ledger.")
        if problems:
            raise RuntimeError("Invalid configuration:\n - " + "\n - ".join(problems))


settings = Settings()
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
