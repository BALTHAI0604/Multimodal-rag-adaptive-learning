"""A database created by the first version of the project must upgrade in place without losing rows."""
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

LEGACY = Path(__file__).resolve().parents[2] / "legacy_backup" / "adaptive_platform.legacy.db"


def test_legacy_database_upgrades_without_data_loss():
    tmp = Path(tempfile.mkdtemp()) / "legacy.db"
    shutil.copy(LEGACY, tmp)

    def counts():
        c = sqlite3.connect(tmp)
        out = {t: c.execute(f'select count(*) from "{t}"').fetchone()[0]
               for (t,) in c.execute("select name from sqlite_master where type='table'")}
        c.close()
        return out
    before = counts()
    code = ("from app.migrations import ensure_schema; ensure_schema(); "
            "from app.database import SessionLocal; from app.models.models import User; "
            "db=SessionLocal(); print('USERS', db.query(User).count())")
    env = {"ENVIRONMENT": "test", "DATABASE_URL": f"sqlite:///{tmp}", "DATA_DIR": str(tmp.parent), "UPLOAD_DIR": str(tmp.parent),
           "PATH": "/usr/bin:/bin"}
    import os
    env = {**os.environ, **env}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=Path(__file__).resolve().parents[1])
    assert r.returncode == 0, r.stderr[-1500:]
    after = counts()
    for t, n in before.items():
        assert after[t] == n, f"rows lost in {t}"
    assert after.get("audit_logs") is not None and "USERS" in r.stdout
