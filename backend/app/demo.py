"""DEVELOPMENT-ONLY sample data so the UI is not empty on a fresh laptop.

Creates a small institution structure, one account per role and ONE sample topic with sample lecture text.
Passwords are random and printed once; nothing here is a real credential and nothing is created in production.
The sample text is generic placeholder study material, not course content: faculty should upload their own."""
from __future__ import annotations

from pathlib import Path

from app.auth.providers import get_provider
from app.config import settings
from app.database import SessionLocal
from app.gamification.engine import ensure_badges
from app.migrations import ensure_schema
from app.models.models import (
    ClassGroup, Department, Faculty, LearningMaterial, Student, Subject, SubjectAssignment, Topic, Unit, User,
)
from app.rag import pipeline

SAMPLE = """Photosynthesis converts light energy into chemical energy.
Chlorophyll in the chloroplast absorbs light, mainly red and blue wavelengths. Photosynthesis is the process by which plants make glucose from carbon dioxide and water.

The light reactions occur in the thylakoid membranes. Water is split, oxygen is released, and ATP and NADPH are produced.

The Calvin cycle occurs in the stroma. Carbon dioxide is fixed by the enzyme RuBisCO. The Calvin cycle uses ATP and NADPH to build glucose.

Steps of photosynthesis:
1. Light is absorbed by chlorophyll.
2. Water is split and oxygen is released.
3. ATP and NADPH are formed.
4. Carbon dioxide is fixed in the Calvin cycle.
5. Glucose is synthesised.

Stomata are pores on the leaf surface. Stomata allow carbon dioxide to enter and oxygen to leave. Transpiration is the loss of water vapour through the stomata.
"""


def seed_demo() -> None:
    ensure_schema()
    prov = get_provider()
    out = []
    with SessionLocal() as db:
        ensure_badges(db)
        if db.query(User).filter_by(email="admin@example.edu").first():
            print("Demo data already present.")
            return

        def mk(email, name, role):
            acct = prov.create_account(email, name, None)
            u = User(email=email, username=email, full_name=name, role=role, firebase_uid=acct["uid"],
                     hashed_password=acct["password_hash"], is_active=True, email_verified=True)
            db.add(u)
            db.flush()
            out.append((role, email, acct["temp_password"]))
            return u
        db.add(Department(code="SCI", name="Sciences"))
        admin = mk("admin@example.edu", "Demo Admin", "admin")
        mk("management@example.edu", "Demo Management", "management")
        adv = mk("advisor@example.edu", "Demo Advisor", "advisor")
        fu = mk("faculty@example.edu", "Demo Faculty", "faculty")
        fac = Faculty(user_id=fu.id, department="SCI", employee_id="EMP001")
        db.add(fac)
        cls = ClassGroup(name="SCI-A", department_code="SCI", semester=1, advisor_user_id=adv.id)
        db.add(cls)
        db.flush()
        for i in range(1, 7):
            su = mk(f"student{i}@example.edu", f"Demo Student {i}", "student")
            db.add(Student(user_id=su.id, roll_number=f"S{i:03d}", department="SCI", semester=1, class_id=cls.id,
                           class_name=cls.name, advisor_id=adv.id))
        sub = Subject(code="BIO101", name="Introductory Biology", department="SCI", semester=1, faculty_id=fac.id)
        db.add(sub)
        db.flush()
        unit = Unit(subject_id=sub.id, unit_number=1, title="Plant Physiology")
        db.add(unit)
        db.flush()
        topic = Topic(unit_id=unit.id, title="Photosynthesis", order_index=1, is_today_topic=True, today_class_id=cls.id)
        db.add(topic)
        db.flush()
        store = settings.UPLOAD_DIR / "materials"
        store.mkdir(parents=True, exist_ok=True)
        p = store / "demo-photosynthesis.txt"
        p.write_text(SAMPLE, encoding="utf-8")
        m = LearningMaterial(subject_id=sub.id, unit_id=unit.id, topic_id=topic.id, faculty_id=fac.id,
                             title="Sample notes: Photosynthesis", material_type="notes", file_path=str(p),
                             file_name=p.name, file_size=p.stat().st_size, status="uploaded", is_approved=True)
        db.add(m)
        db.commit()
        pipeline.process_material(db, m.id)
    print("Demo accounts (one-time random passwords, development only):")
    for role, email, pw in out:
        print(f"  {role:<10} {email:<28} {pw}")
