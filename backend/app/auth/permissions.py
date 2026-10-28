"""Centralised authorization rules.  Every route that touches private data goes through here.

The identity and role ALWAYS come from the server-side `User` row; nothing the client sends
(role, student id, subject id) is trusted without being checked against these functions.
"""
from __future__ import annotations

from typing import Optional, Set

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.models import (
    Assessment, ClassGroup, Faculty, LearningMaterial, Student, Subject, SubjectAssignment, User,
)


def forbidden(msg: str = "You do not have permission to access this resource.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=msg)


def not_found(what: str = "Resource") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found.")


# ---------------------------------------------------------------- profiles
def student_of(db: Session, user: User) -> Student:
    if user.role != "student":
        raise forbidden("Student account required.")
    st = db.query(Student).filter(Student.user_id == user.id).first()
    if not st:
        raise forbidden("No student profile is linked to this account.")
    return st


def faculty_of(db: Session, user: User) -> Faculty:
    if user.role != "faculty":
        raise forbidden("Faculty account required.")
    f = db.query(Faculty).filter(Faculty.user_id == user.id).first()
    if not f:
        raise forbidden("No faculty profile is linked to this account.")
    return f


# ---------------------------------------------------------------- subject scopes
def faculty_subject_ids(db: Session, faculty: Faculty) -> Set[int]:
    ids = {s.id for s in db.query(Subject.id).filter(Subject.faculty_id == faculty.id)}
    ids |= {a.subject_id for a in db.query(SubjectAssignment.subject_id).filter(SubjectAssignment.faculty_id == faculty.id)}
    return ids


def faculty_class_scope(db: Session, faculty: Faculty, subject: Subject):
    """Returns (covers_all_classes, set_of_class_ids) for this faculty on this subject."""
    if subject.faculty_id == faculty.id:
        return True, set()
    rows = db.query(SubjectAssignment).filter(
        SubjectAssignment.subject_id == subject.id, SubjectAssignment.faculty_id == faculty.id).all()
    if any(r.class_id is None for r in rows):
        return True, set()
    return False, {r.class_id for r in rows}


def require_subject_faculty(db: Session, user: User, subject_id: int) -> Subject:
    """Faculty must be assigned to the subject.  (Management/admin do NOT get row-level access.)"""
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject:
        raise not_found("Subject")
    if user.role != "faculty":
        raise forbidden("Only the subject faculty can access this resource.")
    f = faculty_of(db, user)
    if subject.id not in faculty_subject_ids(db, f):
        raise forbidden(f"You are not assigned to subject {subject.code}.")
    return subject


def students_in_subject_scope(db: Session, subject: Subject, faculty: Optional[Faculty] = None):
    """Query of students who take `subject` (limited to the faculty's classes when given)."""
    q = db.query(Student).filter(Student.department == subject.department)
    if subject.semester:
        q = q.filter(or_(Student.semester == subject.semester, Student.semester.is_(None)))
    if faculty is not None:
        all_classes, class_ids = faculty_class_scope(db, faculty, subject)
        if not all_classes:
            q = q.filter(Student.class_id.in_(class_ids or {-1}))
    else:
        has_cls = db.query(SubjectAssignment).filter(SubjectAssignment.subject_id == subject.id).count() > 0
        if has_cls and not subject.faculty_id:
            ids = {a.class_id for a in db.query(SubjectAssignment).filter(SubjectAssignment.subject_id == subject.id)}
            if None not in ids:
                q = q.filter(Student.class_id.in_(ids))
    return q


def subject_covers_student(db: Session, subject: Subject, student: Student) -> bool:
    if subject.department != student.department:
        return False
    if subject.semester and student.semester and subject.semester != student.semester:
        return False
    assigns = db.query(SubjectAssignment).filter(SubjectAssignment.subject_id == subject.id).all()
    class_scoped = [a for a in assigns if a.class_id is not None]
    if not class_scoped or subject.faculty_id or any(a.class_id is None for a in assigns):
        return True
    return student.class_id in {a.class_id for a in class_scoped}


def student_subject_ids(db: Session, student: Student) -> Set[int]:
    subs = db.query(Subject).filter(Subject.department == student.department).all()
    return {s.id for s in subs if subject_covers_student(db, s, student)}


def require_student_subject(db: Session, student: Student, subject_id: int) -> Subject:
    subject = db.query(Subject).filter(Subject.id == subject_id).first()
    if not subject or not subject_covers_student(db, subject, student):
        raise forbidden("This subject is not part of your enrolled courses.")
    return subject


# ---------------------------------------------------------------- person-to-person scopes
def advisor_student_ids(db: Session, user: User) -> Set[int]:
    class_ids = [c.id for c in db.query(ClassGroup.id).filter(ClassGroup.advisor_user_id == user.id)]
    q = db.query(Student.id).filter(or_(Student.advisor_id == user.id,
                                        Student.class_id.in_(class_ids) if class_ids else False))
    return {r.id for r in q}


def require_advisor_student(db: Session, user: User, student_id: int) -> Student:
    if user.role != "advisor":
        raise forbidden("Only the assigned advisor can access this student.")
    if student_id not in advisor_student_ids(db, user):
        raise forbidden("This student is not assigned to you.")
    st = db.query(Student).filter(Student.id == student_id).first()
    if not st:
        raise not_found("Student")
    return st


def faculty_can_see_student(db: Session, user: User, student: Student, subject_id: int) -> Subject:
    subject = require_subject_faculty(db, user, subject_id)
    f = faculty_of(db, user)
    if not students_in_subject_scope(db, subject, f).filter(Student.id == student.id).first():
        raise forbidden("This student is not in a class you teach for this subject.")
    return subject


# ---------------------------------------------------------------- materials / assessments
def material_visible_to_student(db: Session, material: LearningMaterial, student: Student) -> bool:
    if not (material.is_approved and material.status == "indexed"):
        return False
    if material.class_id is not None and material.class_id != student.class_id:
        return False
    subject = db.query(Subject).filter(Subject.id == material.subject_id).first()
    return bool(subject and subject_covers_student(db, subject, student))


def assessment_visible_to_student(db: Session, a: Assessment, student: Student) -> bool:
    if a.owner_student_id is not None:
        return a.owner_student_id == student.id
    if a.status not in ("published", "closed"):
        return False
    if a.class_id is not None and a.class_id != student.class_id:
        return False
    subject = db.query(Subject).filter(Subject.id == a.subject_id).first()
    return bool(subject and subject_covers_student(db, subject, student))
