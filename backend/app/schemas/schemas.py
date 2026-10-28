"""Pydantic request models.  `extra="forbid"` everywhere so unknown fields (e.g. a client trying to
send `role` or `student_id`) are rejected instead of silently ignored -> no mass assignment."""
import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Role = Literal["student", "faculty", "advisor", "management", "admin"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------- auth
class LoginRequest(Strict):
    identifier: str = Field(min_length=3, max_length=254)        # e-mail or institution ID
    password: str = Field(min_length=1, max_length=256)


class IdentifierRequest(Strict):
    identifier: str = Field(min_length=3, max_length=254)


# ---------------------------------------------------------------- admin
class StudentProfileIn(Strict):
    roll_number: str = Field(min_length=3, max_length=50)
    department: str = Field(min_length=1, max_length=100)
    semester: int = Field(ge=1, le=12)
    class_id: Optional[int] = None
    advisor_user_id: Optional[int] = None


class FacultyProfileIn(Strict):
    employee_id: str = Field(min_length=2, max_length=50)
    department: str = Field(min_length=1, max_length=100)
    designation: str = Field(default="Faculty", max_length=100)


class AdminCreateUser(Strict):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    role: Role
    student: Optional[StudentProfileIn] = None
    faculty: Optional[FacultyProfileIn] = None


class AdminUpdateUser(Strict):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    role: Optional[Role] = None
    is_active: Optional[bool] = None


class AdminUpdateStudent(Strict):
    class_id: Optional[int] = None
    advisor_user_id: Optional[int] = None
    semester: Optional[int] = Field(default=None, ge=1, le=12)
    extra_time_percent: Optional[int] = Field(default=None, ge=0, le=100)
    exempt_fullscreen: Optional[bool] = None
    typed_voice_alternative: Optional[bool] = None


class DepartmentIn(Strict):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=2, max_length=200)


class ClassIn(Strict):
    name: str = Field(min_length=1, max_length=50)
    department_code: str = Field(min_length=1, max_length=20)
    semester: int = Field(ge=1, le=12)
    academic_year: Optional[str] = Field(default=None, max_length=20)
    advisor_user_id: Optional[int] = None


class SubjectIn(Strict):
    code: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=2, max_length=200)
    department: str = Field(min_length=1, max_length=100)
    semester: int = Field(ge=1, le=12)
    description: Optional[str] = Field(default=None, max_length=2000)


class AssignmentIn(Strict):
    subject_id: int
    faculty_id: int
    class_id: Optional[int] = None


# ---------------------------------------------------------------- curriculum
class UnitIn(Strict):
    unit_number: int = Field(ge=1, le=50)
    title: str = Field(min_length=2, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)


class TopicIn(Strict):
    title: str = Field(min_length=2, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    order_index: Optional[int] = Field(default=None, ge=1, le=500)


class TodayTopicRequest(Strict):
    topic_id: int
    class_id: Optional[int] = None
    is_today: bool = True


class MaterialUpdate(Strict):
    title: Optional[str] = Field(default=None, min_length=2, max_length=255)
    is_approved: Optional[bool] = None
    unit_id: Optional[int] = None
    topic_id: Optional[int] = None
    class_id: Optional[int] = None
    material_type: Optional[str] = Field(default=None, max_length=50)


# ---------------------------------------------------------------- assessments
class GenerateRequest(Strict):
    subject_id: int
    unit_id: int
    topic_id: int
    class_id: Optional[int] = None
    title: str = Field(min_length=3, max_length=255)
    num_questions: int = Field(default=8, ge=1, le=30)
    types: Optional[List[str]] = None
    bloom_focus: str = "mixed"
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    time_limit_minutes: int = Field(default=20, ge=1, le=240)
    voice_prep_sec: Optional[int] = Field(default=None, ge=0, le=120)
    voice_max_time_sec: Optional[int] = Field(default=None, ge=15, le=600)


class AssessmentSettings(Strict):
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    class_id: Optional[int] = None
    time_limit_minutes: Optional[int] = Field(default=None, ge=1, le=240)
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    answers_release_at: Optional[datetime.datetime] = None
    negative_marking: Optional[float] = Field(default=None, ge=0, le=1)
    shuffle_questions: Optional[bool] = None
    shuffle_options: Optional[bool] = None
    max_attempts: Optional[int] = Field(default=None, ge=1, le=5)
    show_score_immediately: Optional[bool] = None
    require_fullscreen: Optional[bool] = None
    require_preflight: Optional[bool] = None
    integrity_policy: Optional[Literal["log", "warn", "pause", "submit"]] = None
    max_violations: Optional[int] = Field(default=None, ge=1, le=20)
    pass_percentage: Optional[float] = Field(default=None, ge=0, le=100)


class OptionIn(Strict):
    option_text: str = Field(min_length=1, max_length=1000)
    is_correct: bool = False
    explanation: Optional[str] = Field(default=None, max_length=1000)
    misconception: Optional[str] = Field(default=None, max_length=500)


class QuestionIn(Strict):
    question_type: str
    question_text: str = Field(min_length=5, max_length=3000)
    bloom_level: str = "understand"
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    marks: float = Field(default=1, gt=0, le=20)
    explanation: Optional[str] = Field(default=None, max_length=2000)
    options: List[OptionIn] = []
    match_pairs_json: Optional[List[Dict[str, str]]] = None
    sequence_items_json: Optional[List[Dict[str, str]]] = None
    assertion_text: Optional[str] = Field(default=None, max_length=1000)
    reason_text: Optional[str] = Field(default=None, max_length=1000)
    accepted_answers_json: Optional[List[str]] = None
    numeric_answer: Optional[float] = None
    numeric_tolerance: Optional[float] = Field(default=None, ge=0)
    answer_key_text: Optional[str] = Field(default=None, max_length=4000)
    rubric_json: Optional[Dict[str, Any]] = None
    voice_guidance: Optional[str] = Field(default=None, max_length=1000)
    voice_prep_sec: Optional[int] = Field(default=None, ge=0, le=120)
    voice_max_time_sec: Optional[int] = Field(default=None, ge=15, le=600)


class PublishRequest(Strict):
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None


# ---------------------------------------------------------------- student attempts
class PreflightIn(Strict):
    camera_ok: bool = False
    mic_ok: bool = False
    mic_level: Optional[float] = Field(default=None, ge=0, le=1)
    sample_sentence_ok: bool = False
    camera_consent: bool = False
    fullscreen_supported: bool = True


class StartAttemptRequest(Strict):
    preflight: Optional[PreflightIn] = None


class AnswerIn(Strict):
    question_id: int
    selected_option_id: Optional[str] = Field(default=None, max_length=40)
    answer_text: Optional[str] = Field(default=None, max_length=8000)
    answer_json: Optional[Dict[str, Any]] = None
    input_mode: Optional[Literal["typed", "voice"]] = None


class SubmitRequest(Strict):
    answers: List[AnswerIn] = []


class ViolationIn(Strict):
    violation_type: Literal["tab_hidden", "window_blur", "fullscreen_exit", "copy_paste", "camera_lost", "mic_lost"]
    client_time: Optional[str] = Field(default=None, max_length=40)
    detail: Optional[str] = Field(default=None, max_length=300)


class PrivacyUpdate(Strict):
    leaderboard_opt_in: Optional[bool] = None
    display_alias: Optional[str] = Field(default=None, min_length=2, max_length=20, pattern=r"^[A-Za-z0-9 _.-]+$")
    conversation_retention_days: Optional[int] = Field(default=None, ge=0, le=365)


class ShareRequest(Strict):
    confirm: bool
    include_name: bool = False


class PracticeRequest(Strict):
    topic_id: int
    count: int = Field(default=5, ge=3, le=15)


# ---------------------------------------------------------------- reviews / grading
class GradeOverride(Strict):
    score: float = Field(ge=0)
    comment: Optional[str] = Field(default=None, max_length=1000)


class ViolationReview(Strict):
    review_status: Literal["dismissed", "confirmed"]
    note: Optional[str] = Field(default=None, max_length=500)


class TeacherReviewIn(Strict):
    student_id: int
    subject_id: int
    unit_id: Optional[int] = None
    rating: Literal["excellent", "very_good", "good", "satisfactory", "needs_improvement"]
    comment: str = Field(min_length=3, max_length=2000)
    follow_up: bool = False


class FollowUpUpdate(Strict):
    follow_up_status: Literal["open", "done"]
    follow_up_note: Optional[str] = Field(default=None, max_length=1000)


class AdvisorReviewIn(Strict):
    student_id: int
    observation: str = Field(min_length=3, max_length=2000)
    recommendation: str = Field(min_length=3, max_length=2000)
    intervention_required: bool = False
    intervention_plan: Optional[str] = Field(default=None, max_length=2000)


class InterventionUpdate(Strict):
    intervention_status: Literal["open", "in_progress", "closed"]
    intervention_plan: Optional[str] = Field(default=None, max_length=2000)


# ---------------------------------------------------------------- RAG
class CompanionRequest(Strict):
    message: str = Field(min_length=1, max_length=2000)
    language: Literal["English", "Hindi", "Tamil", "Kannada", "Malayalam"] = "English"
    subject_id: Optional[int] = None
    topic_id: Optional[int] = None
    session_id: Optional[str] = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    is_voice: bool = False
    allow_general: bool = False


class TTSRequest(Strict):
    text: str = Field(min_length=1, max_length=2000)
    language: Literal["English", "Hindi", "Tamil", "Kannada", "Malayalam"] = "English"


class VerifyRequest(Strict):
    record_ref: str = Field(min_length=4, max_length=64)
