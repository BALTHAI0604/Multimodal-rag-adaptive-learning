"""Relational schema (SQLAlchemy).

Every new column added on top of the original project is nullable or has a default so that
`app.migrations.ensure_schema` can upgrade an existing database in place without data loss.
All timestamps are naive UTC.
"""
import datetime

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, LargeBinary,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.security.crypto import EncryptedText


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------
ROLES = ("student", "faculty", "advisor", "management", "admin")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    firebase_uid = Column(String(128), unique=True, index=True, nullable=True)
    username = Column(String(100), unique=True, index=True, nullable=True)       # legacy; = email in new rows
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=True)                         # bcrypt; AUTH_MODE=local only
    full_name = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False)
    avatar = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    email_verified = Column(Boolean, default=False)
    token_valid_after = Column(DateTime, nullable=True)       # tokens issued before this are rejected
    failed_login_count = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    created_by_id = Column(Integer, nullable=True)
    deactivated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    student_profile = relationship("Student", back_populates="user", uselist=False, foreign_keys="[Student.user_id]")
    faculty_profile = relationship("Faculty", back_populates="user", uselist=False, foreign_keys="[Faculty.user_id]")


class RefreshSession(Base):
    """Opaque refresh tokens for AUTH_MODE=local (Firebase manages its own in firebase mode)."""
    __tablename__ = "refresh_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False)
    name = Column(String(200), nullable=False)


class ClassGroup(Base):
    __tablename__ = "class_groups"
    __table_args__ = (UniqueConstraint("name", "department_code", name="uq_class_name_dept"),)

    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    department_code = Column(String(20), nullable=False)
    semester = Column(Integer, default=1)
    academic_year = Column(String(20), nullable=True)
    advisor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)


class Student(Base):
    __tablename__ = "students"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    roll_number = Column(String(50), unique=True, index=True, nullable=False)      # institution-issued student ID
    department = Column(String(100), nullable=False)
    semester = Column(Integer, default=1)
    class_name = Column(String(50), nullable=True)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=True)
    advisor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    total_xp = Column(Integer, default=0)
    current_level = Column(Integer, default=1)
    streak_days = Column(Integer, default=0)
    longest_streak = Column(Integer, default=0)
    last_active_date = Column(DateTime, nullable=True)                              # legacy
    streak_last_date = Column(Date, nullable=True)
    timezone = Column(String(64), nullable=True)
    leaderboard_opt_in = Column(Boolean, default=False)
    display_alias = Column(String(40), nullable=True)
    conversation_retention_days = Column(Integer, default=90)                       # 0 = do not store chats
    # approved accommodations (set by admin/advisor)
    extra_time_percent = Column(Integer, default=0)
    exempt_fullscreen = Column(Boolean, default=False)
    typed_voice_alternative = Column(Boolean, default=False)

    user = relationship("User", back_populates="student_profile", foreign_keys=[user_id])
    advisor = relationship("User", foreign_keys=[advisor_id])
    class_group = relationship("ClassGroup", foreign_keys=[class_id])
    attempts = relationship("AssessmentAttempt", back_populates="student")
    mastery = relationship("TopicMastery", back_populates="student")
    recommendations = relationship("Recommendation", back_populates="student")
    badges = relationship("StudentBadge", back_populates="student")


class Faculty(Base):
    __tablename__ = "faculty"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    department = Column(String(100), nullable=False)
    designation = Column(String(100), default="Faculty")
    employee_id = Column(String(50), unique=True, nullable=False)

    user = relationship("User", back_populates="faculty_profile")
    subjects = relationship("Subject", back_populates="faculty")


# --------------------------------------------------------------------------------------
# Curriculum
# --------------------------------------------------------------------------------------
class Subject(Base):
    __tablename__ = "subjects"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=False)
    department = Column(String(100), nullable=False)
    semester = Column(Integer, default=1)
    faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=True)           # primary handler (legacy)
    description = Column(Text, nullable=True)

    faculty = relationship("Faculty", back_populates="subjects")
    units = relationship("Unit", back_populates="subject", cascade="all, delete-orphan", order_by="Unit.unit_number")
    learning_materials = relationship("LearningMaterial", back_populates="subject")
    assessments = relationship("Assessment", back_populates="subject")


class SubjectAssignment(Base):
    """Which faculty teaches which subject to which class (class_id NULL = every class of the subject)."""
    __tablename__ = "subject_assignments"
    __table_args__ = (UniqueConstraint("subject_id", "faculty_id", "class_id", name="uq_subject_assignment"),)

    id = Column(Integer, primary_key=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False, index=True)
    faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False, index=True)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)


class Unit(Base):
    __tablename__ = "units"

    id = Column(Integer, primary_key=True, index=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    unit_number = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    subject = relationship("Subject", back_populates="units")
    topics = relationship("Topic", back_populates="unit", cascade="all, delete-orphan", order_by="Topic.order_index")


class Topic(Base):
    __tablename__ = "topics"

    id = Column(Integer, primary_key=True, index=True)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    order_index = Column(Integer, default=1)
    is_today_topic = Column(Boolean, default=False)
    today_date = Column(DateTime, nullable=True)
    today_class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=True)   # topic published for this class

    unit = relationship("Unit", back_populates="topics")
    learning_materials = relationship("LearningMaterial", back_populates="topic")
    assessments = relationship("Assessment", back_populates="topic")


# --------------------------------------------------------------------------------------
# Learning materials & RAG
# --------------------------------------------------------------------------------------
class LearningMaterial(Base):
    __tablename__ = "learning_materials"

    id = Column(Integer, primary_key=True, index=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False)
    faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=True)         # NULL = all classes of subject
    title = Column(String(255), nullable=False)
    material_type = Column(String(50), default="notes")
    file_path = Column(String(500), nullable=True)                                   # storage key, never the client file name
    file_name = Column(String(255), nullable=True)                                   # original (display only)
    file_size = Column(Integer, default=0)
    content_type = Column(String(100), nullable=True)
    sha256 = Column(String(64), nullable=True)
    content_text = Column(Text, nullable=True)
    status = Column(String(50), default="uploaded")        # uploaded | processing | indexed | error
    error_message = Column(Text, nullable=True)
    is_approved = Column(Boolean, default=True)            # faculty-approved for students & question generation
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    subject = relationship("Subject", back_populates="learning_materials")
    topic = relationship("Topic", back_populates="learning_materials")
    documents = relationship("Document", back_populates="material", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    material_id = Column(Integer, ForeignKey("learning_materials.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    file_type = Column(String(50), nullable=False)
    file_path = Column(String(500), nullable=False)
    status = Column(String(50), default="processed")
    error_message = Column(Text, nullable=True)
    num_chunks = Column(Integer, default=0)
    modalities = Column(JSON, nullable=True)               # e.g. ["text","ocr","table","transcript"]
    created_at = Column(DateTime, default=utcnow)

    material = relationship("LearningMaterial", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (Index("ix_chunk_scope", "subject_id", "topic_id"),)

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("learning_materials.id"), nullable=True, index=True)
    subject_id = Column(Integer, nullable=True)
    unit_id = Column(Integer, nullable=True)
    topic_id = Column(Integer, nullable=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    section = Column(String(255), nullable=True)
    page_ref = Column(String(50), nullable=True)
    modality = Column(String(30), default="text")
    embedding = Column(LargeBinary, nullable=True)         # float32 bytes
    embedding_model = Column(String(120), nullable=True)
    embedding_json = Column(Text, nullable=True)           # legacy
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    document = relationship("Document", back_populates="chunks")


# --------------------------------------------------------------------------------------
# Assessments
# --------------------------------------------------------------------------------------
class Assessment(Base):
    __tablename__ = "assessments"

    id = Column(Integer, primary_key=True, index=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False)
    faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False)
    class_id = Column(Integer, ForeignKey("class_groups.id"), nullable=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    kind = Column(String(20), default="daily")                  # daily | practice
    owner_student_id = Column(Integer, ForeignKey("students.id"), nullable=True)   # practice sets are private
    status = Column(String(20), default="draft")                # draft | published | closed
    activity_key = Column(String(200), unique=True, nullable=True)
    time_limit_minutes = Column(Integer, default=20)
    total_marks = Column(Float, default=0)
    pass_marks = Column(Float, default=0)
    is_published = Column(Boolean, default=False)               # legacy mirror of status == published
    published_at = Column(DateTime, nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    bloom_level_focus = Column(String(50), default="mixed")
    difficulty_target = Column(String(50), default="medium")
    negative_marking = Column(Float, default=0.0)               # fraction of a question's marks deducted for a wrong objective answer
    shuffle_questions = Column(Boolean, default=True)
    shuffle_options = Column(Boolean, default=True)
    max_attempts = Column(Integer, default=1)
    answers_release_at = Column(DateTime, nullable=True)        # NULL => when window ends / faculty closes
    show_score_immediately = Column(Boolean, default=True)
    require_fullscreen = Column(Boolean, default=False)
    require_preflight = Column(Boolean, default=False)
    integrity_policy = Column(String(20), default="warn")       # log | warn | pause | submit
    max_violations = Column(Integer, default=3)
    source_material_ids = Column(JSON, nullable=True)
    generation_info = Column(JSON, nullable=True)
    approved_by_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    subject = relationship("Subject", back_populates="assessments")
    topic = relationship("Topic", back_populates="assessments")
    questions = relationship("Question", back_populates="assessment", cascade="all, delete-orphan",
                             order_by="Question.order_index")
    attempts = relationship("AssessmentAttempt", back_populates="assessment")


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False)
    order_index = Column(Integer, default=0)
    question_text = Column(Text, nullable=False)
    question_type = Column(String(50), nullable=False)
    bloom_level = Column(String(50), default="remember")
    marks = Column(Float, default=2)
    difficulty = Column(String(50), default="medium")
    explanation = Column(Text, nullable=True)
    match_pairs_json = Column(JSON, nullable=True)              # [{"left":..,"right":..}] correct pairing
    sequence_items_json = Column(JSON, nullable=True)           # items in CORRECT order: [{"text":..}]
    assertion_text = Column(Text, nullable=True)
    reason_text = Column(Text, nullable=True)
    answer_key_text = Column(Text, nullable=True)               # model answer for text/voice questions
    accepted_answers_json = Column(JSON, nullable=True)         # fill-in-the-blank accepted strings
    numeric_answer = Column(Float, nullable=True)
    numeric_tolerance = Column(Float, nullable=True)
    rubric_json = Column(JSON, nullable=True)                   # {"key_points":[{text,keywords,weight}],"misconceptions":[...]}
    voice_guidance = Column(Text, nullable=True)
    voice_prep_sec = Column(Integer, default=10)
    voice_min_time_sec = Column(Integer, default=0)
    voice_max_time_sec = Column(Integer, default=120)
    sample_ideal_answer = Column(Text, nullable=True)           # legacy alias of answer_key_text
    source_refs_json = Column(JSON, nullable=True)
    generation_method = Column(String(30), nullable=True)       # llm | extractive-template | manual
    content_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    assessment = relationship("Assessment", back_populates="questions")
    options = relationship("QuestionOption", back_populates="question", cascade="all, delete-orphan",
                           order_by="QuestionOption.id")
    student_answers = relationship("StudentAnswer", back_populates="question")


class QuestionOption(Base):
    __tablename__ = "question_options"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    option_text = Column(Text, nullable=False)
    is_correct = Column(Boolean, default=False)
    explanation = Column(Text, nullable=True)
    misconception = Column(Text, nullable=True)                 # what choosing this distractor reveals

    question = relationship("Question", back_populates="options")


class AssessmentAttempt(Base):
    __tablename__ = "assessment_attempts"
    __table_args__ = (UniqueConstraint("assessment_id", "student_id", "attempt_no", name="uq_attempt_no"),)

    id = Column(Integer, primary_key=True, index=True)
    assessment_id = Column(Integer, ForeignKey("assessments.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    attempt_no = Column(Integer, default=1)
    seed = Column(Integer, default=0)
    started_at = Column(DateTime, default=utcnow)
    deadline_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    total_score = Column(Float, default=0.0)
    max_score = Column(Float, default=0.0)
    percentage = Column(Float, default=0.0)
    status = Column(String(50), default="in_progress")          # in_progress | completed | auto_submitted | abandoned
    violation_count = Column(Integer, default=0)
    integrity_review_required = Column(Boolean, default=False)
    needs_review = Column(Boolean, default=False)               # contains answers awaiting faculty review
    preflight_json = Column(JSON, nullable=True)
    grade_version = Column(Integer, default=1)
    blockchain_hash = Column(String(128), nullable=True)        # legacy mirror
    blockchain_tx_id = Column(String(128), nullable=True)       # legacy mirror
    integrity_status = Column(String(50), default="pending")

    assessment = relationship("Assessment", back_populates="attempts")
    student = relationship("Student", back_populates="attempts")
    answers = relationship("StudentAnswer", back_populates="attempt", cascade="all, delete-orphan")
    violations = relationship("AssessmentViolation", back_populates="attempt", cascade="all, delete-orphan")


class StudentAnswer(Base):
    __tablename__ = "student_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_answer_per_question"),)

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(Integer, ForeignKey("assessment_attempts.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    answer_text = Column(Text, nullable=True)
    answer_json = Column(JSON, nullable=True)                   # structured answers (match / sequence)
    selected_option_id = Column(Integer, ForeignKey("question_options.id"), nullable=True)
    input_mode = Column(String(20), nullable=True)              # typed | voice
    score_awarded = Column(Float, default=0.0)
    max_score = Column(Float, default=0.0)
    is_correct = Column(Boolean, default=False)
    ai_feedback = Column(Text, nullable=True)
    evaluation_method = Column(String(40), nullable=True)
    evaluation_detail = Column(JSON, nullable=True)
    needs_review = Column(Boolean, default=False)
    faculty_score_override = Column(Float, nullable=True)
    faculty_comment = Column(Text, nullable=True)
    reviewed_by_id = Column(Integer, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    saved_at = Column(DateTime, default=utcnow)
    evaluated_at = Column(DateTime, nullable=True)

    attempt = relationship("AssessmentAttempt", back_populates="answers")
    question = relationship("Question", back_populates="student_answers")
    voice_answer = relationship("VoiceAnswer", back_populates="student_answer", uselist=False,
                                cascade="all, delete-orphan")


class VoiceAnswer(Base):
    __tablename__ = "voice_answers"

    id = Column(Integer, primary_key=True, index=True)
    student_answer_id = Column(Integer, ForeignKey("student_answers.id"), unique=True, nullable=False)
    audio_file_path = Column(String(500), nullable=True)
    audio_deleted_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, default=0.0)
    language = Column(String(20), nullable=True)
    stt_engine = Column(String(60), nullable=True)
    transcript = Column(EncryptedText, nullable=False)
    conceptual_correctness_score = Column(Float, default=0.0)
    relevance_score = Column(Float, default=0.0)
    completeness_score = Column(Float, default=0.0)
    identified_concepts = Column(JSON, nullable=True)
    missing_concepts = Column(JSON, nullable=True)
    detected_misconceptions = Column(JSON, nullable=True)
    speech_clarity_score = Column(Float, nullable=True)         # deliberately unused for grading
    ai_evaluation_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    student_answer = relationship("StudentAnswer", back_populates="voice_answer")


class AssessmentViolation(Base):
    __tablename__ = "assessment_violations"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(Integer, ForeignKey("assessment_attempts.id"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    violation_type = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=utcnow)                # server time
    client_timestamp = Column(String(40), nullable=True)
    severity = Column(String(50), default="low")
    review_status = Column(String(20), default="pending")      # pending | dismissed | confirmed
    reviewed_by_id = Column(Integer, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_note = Column(Text, nullable=True)

    attempt = relationship("AssessmentAttempt", back_populates="violations")


# --------------------------------------------------------------------------------------
# Adaptive learning
# --------------------------------------------------------------------------------------
class TopicMastery(Base):
    __tablename__ = "topic_mastery"
    __table_args__ = (UniqueConstraint("student_id", "topic_id", name="uq_mastery_student_topic"),)

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False)
    mastery_score = Column(Float, default=0.0)
    avg_score = Column(Float, default=0.0)
    recent_avg = Column(Float, default=0.0)
    attempts_count = Column(Integer, default=0)
    total_questions_attempted = Column(Integer, default=0)
    correct_answers = Column(Integer, default=0)
    current_difficulty = Column(String(50), default="medium")
    trend = Column(String(20), default="insufficient_data")
    completion_status = Column(String(20), default="not_started")
    last_scores_json = Column(JSON, nullable=True)
    last_updated = Column(DateTime, default=utcnow)

    student = relationship("Student", back_populates="mastery")


class Misconception(Base):
    __tablename__ = "misconceptions"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False)
    misconception_text = Column(Text, nullable=False)
    context_question_id = Column(Integer, ForeignKey("questions.id"), nullable=True)
    corrective_explanation = Column(Text, nullable=False)
    occurrence_count = Column(Integer, default=1)
    is_resolved = Column(Boolean, default=False)
    practice_status = Column(String(50), default="pending")
    detected_at = Column(DateTime, default=utcnow)
    last_seen_at = Column(DateTime, default=utcnow)
    resolved_at = Column(DateTime, nullable=True)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    reason = Column(Text, nullable=True)
    activity_type = Column(String(50), default="revise_concept")
    priority = Column(String(50), default="medium")
    estimated_minutes = Column(Integer, default=15)
    status = Column(String(50), default="pending")
    generated_by = Column(String(50), default="ai")             # adaptive | advisor | faculty
    material_ids_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student", back_populates="recommendations")


class RevisionQueue(Base):
    __tablename__ = "revision_queue"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False)
    priority_order = Column(Integer, default=1)
    estimated_time_minutes = Column(Integer, default=15)
    action_type = Column(String(100), default="concept_revision")
    reason = Column(Text, nullable=True)
    material_ids_json = Column(JSON, nullable=True)
    completed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)


class TeacherReview(Base):
    __tablename__ = "teacher_reviews"

    id = Column(Integer, primary_key=True, index=True)
    faculty_id = Column(Integer, ForeignKey("faculty.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=False)
    unit_id = Column(Integer, ForeignKey("units.id"), nullable=True)
    rating = Column(String(50), default="good")
    comment = Column(Text, nullable=False)
    follow_up_status = Column(String(20), default="none")       # none | open | done
    follow_up_note = Column(Text, nullable=True)
    follow_up_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    faculty = relationship("Faculty")
    subject = relationship("Subject")


class AdvisorReview(Base):
    __tablename__ = "advisor_reviews"

    id = Column(Integer, primary_key=True, index=True)
    advisor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    observation = Column(Text, nullable=False)
    recommendation = Column(Text, nullable=False)
    intervention_required = Column(Boolean, default=False)
    intervention_plan = Column(Text, nullable=True)
    intervention_status = Column(String(20), default="none")   # none | open | in_progress | closed
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, nullable=True)


# --------------------------------------------------------------------------------------
# Gamification
# --------------------------------------------------------------------------------------
class XPTransaction(Base):
    __tablename__ = "xp_transactions"
    __table_args__ = (UniqueConstraint("student_id", "source_type", "source_ref", name="uq_xp_once"),)

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    amount = Column(Integer, nullable=False)
    source_type = Column(String(100), nullable=False)
    source_ref = Column(String(100), nullable=True)             # makes each reward claimable exactly once
    description = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utcnow)


class Badge(Base):
    __tablename__ = "badges"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)
    criteria = Column(Text, nullable=True)                      # human readable, explicit criteria
    icon = Column(String(100), default="Award")
    category = Column(String(50), default="academic")
    xp_bonus = Column(Integer, default=50)


class StudentBadge(Base):
    __tablename__ = "student_badges"
    __table_args__ = (UniqueConstraint("student_id", "badge_id", name="uq_student_badge"),)

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    badge_id = Column(Integer, ForeignKey("badges.id"), nullable=False)
    awarded_at = Column(DateTime, default=utcnow)
    reason = Column(String(255), nullable=True)

    student = relationship("Student", back_populates="badges")
    badge = relationship("Badge")


class BadgeShare(Base):
    """A student-approved public card for one badge.  Contains no marks and (optionally) a first name."""
    __tablename__ = "badge_shares"

    id = Column(Integer, primary_key=True)
    student_badge_id = Column(Integer, ForeignKey("student_badges.id"), nullable=False)
    token = Column(String(64), unique=True, nullable=False)
    include_name = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
    revoked_at = Column(DateTime, nullable=True)


# --------------------------------------------------------------------------------------
# AI companion / events
# --------------------------------------------------------------------------------------
class AIConversation(Base):
    __tablename__ = "ai_conversations"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False, index=True)
    session_id = Column(String(40), nullable=True, index=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=True)
    student_query = Column(EncryptedText, nullable=False)
    ai_response = Column(EncryptedText, nullable=False)
    language = Column(String(50), default="English")
    rag_sources_json = Column(JSON, nullable=True)
    answer_type = Column(String(30), nullable=True)
    is_voice_query = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=utcnow)


class LearningEvent(Base):
    __tablename__ = "learning_events"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    event_type = Column(String(100), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)


# --------------------------------------------------------------------------------------
# Blockchain integrity + audit
# --------------------------------------------------------------------------------------
class BlockchainRecord(Base):
    __tablename__ = "blockchain_records"
    __table_args__ = (UniqueConstraint("attempt_id", "version", name="uq_chain_attempt_version"),)

    id = Column(Integer, primary_key=True, index=True)
    record_type = Column(String(100), nullable=False, default="assessment_completion")
    student_id = Column(Integer, ForeignKey("students.id"), nullable=False)
    attempt_id = Column(Integer, ForeignKey("assessment_attempts.id"), nullable=True)
    version = Column(Integer, default=1)
    record_ref = Column(String(64), unique=True, index=True, nullable=True)         # opaque on-chain identifier
    record_id_ref = Column(String(100), nullable=True)                              # legacy
    encrypted_payload = Column(EncryptedText, nullable=True)
    payload_sha256_hash = Column(String(128), index=True, nullable=False)           # keyed hash (HMAC-SHA256)
    status = Column(String(20), default="pending")          # pending | anchored | failed | superseded | revoked
    block_number = Column(Integer, nullable=True)
    transaction_hash = Column(String(128), index=True, nullable=True)
    chain_id = Column(Integer, nullable=True)
    contract_address = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    verified_status = Column(String(50), default="unverified")
    verified_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class BlockchainVerification(Base):
    __tablename__ = "blockchain_verifications"

    id = Column(Integer, primary_key=True)
    record_id = Column(Integer, ForeignKey("blockchain_records.id"), nullable=False, index=True)
    verified_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    result = Column(String(30), nullable=False)
    live_hash = Column(String(128), nullable=True)
    chain_hash = Column(String(128), nullable=True)
    details_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_role = Column(String(30), nullable=True)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(100), nullable=False)
    resource_id = Column(String(100), nullable=True)
    ip_address = Column(String(50), nullable=True)
    request_id = Column(String(32), nullable=True)
    outcome = Column(String(20), default="success")
    details_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)
