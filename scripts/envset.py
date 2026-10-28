"""Tiny .env helper used by setup/start scripts (works on Windows, macOS, Linux). Never prints secret values.
  python scripts/envset.py init                 create .env from .env.example if missing, generate missing keys
  python scripts/envset.py set KEY VALUE        set or replace one key
  python scripts/envset.py get KEY              print one value (empty if unset)
"""
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV, EXAMPLE = ROOT / ".env", ROOT / ".env.example"


def read() -> list:
    return ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []


def get(key: str) -> str:
    for line in read():
        m = re.match(rf"^{re.escape(key)}=([^#\r\n]*)", line)
        if m:
            return m.group(1).strip()
    return ""


def set_(key: str, value: str) -> None:
    lines, done = read(), False
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}=", line):
            lines[i], done = f"{key}={value}", True
    if not done:
        lines.append(f"{key}={value}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def init() -> None:
    if not ENV.exists():
        ENV.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        print("Created .env from .env.example")
    if not get("DATA_ENCRYPTION_KEY"):
        from cryptography.fernet import Fernet
        set_("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
        print("Generated DATA_ENCRYPTION_KEY (kept only in .env - back it up; losing it makes encrypted data unreadable)")
    if not get("BLOCKCHAIN_HMAC_KEY"):
        set_("BLOCKCHAIN_HMAC_KEY", secrets.token_urlsafe(32))
        print("Generated BLOCKCHAIN_HMAC_KEY")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init":
        init()
    elif cmd == "set" and len(sys.argv) == 4:
        set_(sys.argv[2], sys.argv[3])
    elif cmd == "get" and len(sys.argv) == 3:
        print(get(sys.argv[2]))
    else:
        sys.exit(__doc__)
