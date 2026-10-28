"""Test harness: isolated temp database/uploads, local auth, lexical embedder, fake transcriber,
in-memory Ethereum (eth-tester) for real contract calls.  No network, no paid service."""
import io
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="ragtest_"))
os.environ.update({"ENVIRONMENT": "test", "AUTH_MODE": "local", "DATA_DIR": str(_TMP / "data"),
                   "UPLOAD_DIR": str(_TMP / "uploads"), "EMBEDDING_BACKEND": "hashing", "RATE_LIMIT_ENABLED": "false",
                   "OLLAMA_BASE_URL": "http://127.0.0.1:9", "BLOCKCHAIN_ENABLED": "true", "CONTRACT_ADDRESS": ""})
(_TMP / "data").mkdir(exist_ok=True)
(_TMP / "uploads").mkdir(exist_ok=True)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth.providers import hash_password  # noqa: E402
from app.blockchain import service as chain  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.gamification.engine import ensure_badges  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import (  # noqa: E402
    ClassGroup, Department, Faculty, Student, Subject, SubjectAssignment, Topic, Unit, User,
)
from app.security.ratelimit import limiter  # noqa: E402
from app.speech import stt  # noqa: E402

PW = "Test-Passw0rd!x"

NOTES = """Photosynthesis is the process by which plants make glucose from carbon dioxide and water using light energy.
Chlorophyll absorbs light in the chloroplast. The light reactions occur in the thylakoid membranes where water is split and oxygen is released.
The Calvin cycle occurs in the stroma. Carbon dioxide is fixed by the enzyme RuBisCO because the Calvin cycle needs ATP and NADPH.
Steps of photosynthesis:
1. Light is absorbed by chlorophyll.
2. Water is split and oxygen is released.
3. ATP and NADPH are formed.
4. Carbon dioxide is fixed in the Calvin cycle.
Stomata are pores on the leaf surface. Transpiration is the loss of water vapour through the stomata.
Mitochondria is the organelle that produces ATP by cellular respiration in animal and plant cells.
"""


@pytest.fixture(autouse=True)
def world():
    """Fresh schema + institution for every test."""
    Base.metadata.drop_all(bind=engine)
    from app.migrations import ensure_schema
    ensure_schema()
    limiter.reset()
    chain.reset()
    from app.rag import pipeline
    pipeline.index.loaded = False
    from app.ai.llm_provider import llm_provider
    llm_provider.set_override(None)
    db = SessionLocal()
    ensure_badges(db)
    db.add(Department(code="SCI", name="Sciences"))

    def mk(email, name, role):
        u = User(email=email, username=email, full_name=name, role=role, firebase_uid=f"local-{email}",
                 hashed_password=hash_password(PW), is_active=True, email_verified=True)
        db.add(u)
        db.flush()
        return u
    ids = {}
    admin, mgmt = mk("admin@t.edu", "Admin", "admin"), mk("mgmt@t.edu", "Mgmt", "management")
    adv1, adv2 = mk("adv1@t.edu", "Adv One", "advisor"), mk("adv2@t.edu", "Adv Two", "advisor")
    f1u, f2u = mk("fac1@t.edu", "Fac One", "faculty"), mk("fac2@t.edu", "Fac Two", "faculty")
    f1 = Faculty(user_id=f1u.id, department="SCI", employee_id="E1")
    f2 = Faculty(user_id=f2u.id, department="SCI", employee_id="E2")
    db.add_all([f1, f2])
    ca = ClassGroup(name="A", department_code="SCI", semester=1, advisor_user_id=adv1.id)
    cb = ClassGroup(name="B", department_code="SCI", semester=1, advisor_user_id=adv2.id)
    db.add_all([ca, cb])
    db.flush()
    studs = []
    for i, (cls, adv) in enumerate([(ca, adv1), (ca, adv1), (cb, adv2)], 1):
        su = mk(f"s{i}@t.edu", f"Student {i}", "student")
        st = Student(user_id=su.id, roll_number=f"R{i:03d}", department="SCI", semester=1, class_id=cls.id,
                     class_name=cls.name, advisor_id=adv.id)
        db.add(st)
        studs.append(st)
    bio = Subject(code="BIO101", name="Biology", department="SCI", semester=1, faculty_id=f1.id)
    che = Subject(code="CHE101", name="Chemistry", department="SCI", semester=1, faculty_id=f2.id)
    db.add_all([bio, che])
    db.flush()
    u1, u2 = Unit(subject_id=bio.id, unit_number=1, title="Plants"), Unit(subject_id=che.id, unit_number=1, title="Atoms")
    db.add_all([u1, u2])
    db.flush()
    t1 = Topic(unit_id=u1.id, title="Photosynthesis", order_index=1)
    t2 = Topic(unit_id=u2.id, title="Bonding", order_index=1)
    db.add_all([t1, t2])
    db.commit()
    ids.update(admin=admin.id, bio=bio.id, che=che.id, u1=u1.id, t1=t1.id, u2=u2.id, t2=t2.id, ca=ca.id, cb=cb.id,
               s1=studs[0].id, s2=studs[1].id, s3=studs[2].id, f1=f1.id, f2=f2.id)
    db.close()
    yield ids
    stt.set_transcriber_for_tests(None)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def login(client, email):
    r = client.post("/api/auth/login", json={"identifier": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture()
def H(client):
    cache = {}

    def get(email):
        if email not in cache:
            cache[email] = login(client, email)
        return cache[email]
    return get


@pytest.fixture()
def material(client, H, world):
    """Faculty 1 uploads sample notes for Photosynthesis (indexed with the lexical embedder)."""
    r = client.post("/api/faculty/materials/upload", headers=H("fac1@t.edu"),
                    data={"subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"], "title": "Notes"},
                    files={"file": ("notes.txt", NOTES.encode(), "text/plain")})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "indexed", r.json()
    return r.json()


@pytest.fixture()
def eth():
    """Real contract deployed on an in-memory EVM."""
    from web3 import EthereumTesterProvider, Web3
    w3 = Web3(EthereumTesterProvider())
    art = chain.load_artifact()
    h = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"]).constructor().transact({"from": w3.eth.accounts[0]})
    addr = w3.eth.wait_for_transaction_receipt(h).contractAddress
    contract = w3.eth.contract(address=addr, abi=art["abi"])
    chain.set_test_chain(w3, contract, None)
    yield w3, contract
    chain.reset()


@pytest.fixture()
def published(client, H, world, material):
    """A published assessment generated from the uploaded material."""
    r = client.post("/api/faculty/assessments/generate", headers=H("fac1@t.edu"),
                    json={"subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"],
                          "title": "Daily quiz", "num_questions": 6, "types": ["mcq", "true_false", "fill_blank", "voice"],
                          "class_id": world["ca"]})
    assert r.status_code == 201, r.text
    a = r.json()
    p = client.post(f"/api/faculty/assessments/{a['id']}/publish", headers=H("fac1@t.edu"), json={})
    assert p.status_code == 200, p.text
    return a


def answer_all(client, headers, attempt, questions, correct_from=None):
    """Answer the first option of each option question; text for others."""
    for q in questions:
        body = {"question_id": q["id"]}
        if q.get("options"):
            body["selected_option_id"] = q["options"][0]["id"]
        else:
            body["answer_text"] = "photosynthesis chlorophyll"
        r = client.put(f"/api/student/attempts/{attempt}/answer", headers=headers, json=body)
        assert r.status_code == 200, r.text
