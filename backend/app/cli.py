"""Command line helpers.   python -m app.cli <command>

  init-db            create/upgrade tables (also done at API start-up)
  gen-keys           print fresh DATA_ENCRYPTION_KEY / BLOCKCHAIN_HMAC_KEY values
  create-admin       create the first administrator (prompts for the password if not given)
  deploy-contract    deploy StudentAcademicAudit to BLOCKCHAIN_RPC_URL and print CONTRACT_ADDRESS
  demo-data          DEVELOPMENT ONLY sample structure + accounts with random one-time passwords
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import sys


def init_db(_a=None):
    from app.migrations import ensure_schema
    ensure_schema()
    print("Database ready.")


def gen_keys(_a=None):
    from cryptography.fernet import Fernet
    print("DATA_ENCRYPTION_KEY=" + Fernet.generate_key().decode())
    print("BLOCKCHAIN_HMAC_KEY=" + secrets.token_urlsafe(32))


def create_admin(a):
    from app.auth.providers import AuthError, get_provider
    from app.database import SessionLocal
    from app.migrations import ensure_schema
    from app.models.models import User
    ensure_schema()
    email = a.email.lower()
    provider = get_provider()
    with SessionLocal() as db:
        if db.query(User).filter_by(email=email).first():
            print("That e-mail already exists.")
            return 1
        pw = None
        if provider.name == "local":
            pw = a.password or getpass.getpass("Admin password (min 12 chars): ")
        try:
            acct = provider.create_account(email, a.name, pw)
        except AuthError as exc:
            print(exc.message)
            return 1
        db.add(User(email=email, username=email, full_name=a.name, role="admin", firebase_uid=acct["uid"],
                    hashed_password=acct["password_hash"], is_active=True, email_verified=True))
        db.commit()
    print(f"Administrator {email} created." + (" Use the password-reset flow to set your password." if provider.name == "firebase" else ""))


def deploy_contract(_a=None):
    from app.blockchain import service
    from app.config import settings
    res = service.deploy_contract(settings.BLOCKCHAIN_RPC_URL, settings.BLOCKCHAIN_PRIVATE_KEY)
    print("CONTRACT_ADDRESS=" + res["address"])
    print("Add the line above to your .env, then restart the API.")


def demo_data(_a=None):
    from app.config import settings
    if settings.is_production or settings.AUTH_MODE != "local":
        print("demo-data runs only with ENVIRONMENT!=production and AUTH_MODE=local.")
        return 1
    from app.demo import seed_demo
    seed_demo()


def main(argv=None):
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db").set_defaults(fn=init_db)
    sub.add_parser("gen-keys").set_defaults(fn=gen_keys)
    c = sub.add_parser("create-admin")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="Administrator")
    c.add_argument("--password")
    c.set_defaults(fn=create_admin)
    sub.add_parser("deploy-contract").set_defaults(fn=deploy_contract)
    sub.add_parser("demo-data").set_defaults(fn=demo_data)
    a = p.parse_args(argv)
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
