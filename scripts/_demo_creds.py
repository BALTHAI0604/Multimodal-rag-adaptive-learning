"""Reads one-time demo passwords printed by `python -m app.cli demo-data` (saved by setup to data/demo-accounts.txt)."""
import os
import re
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "data" / "demo-accounts.txt"
KEYS = {"admin@example.edu": "E2E_ADMIN_PW", "management@example.edu": "E2E_MGMT_PW", "advisor@example.edu": "E2E_ADVISOR_PW",
        "faculty@example.edu": "E2E_FACULTY_PW", "student1@example.edu": "E2E_STUDENT1_PW", "student2@example.edu": "E2E_STUDENT2_PW"}


def load() -> None:
    """Fill E2E_*_PW environment variables from the demo-accounts file (explicit env vars win)."""
    if not FILE.exists():
        return
    for line in FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*\w+\s+(\S+@\S+)\s+(\S+)\s*$", line)
        if m and m.group(1) in KEYS:
            os.environ.setdefault(KEYS[m.group(1)], m.group(2))
