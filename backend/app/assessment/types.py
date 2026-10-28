"""Shared constants for question types and Bloom levels."""
QUESTION_TYPES = [
    "mcq", "true_false", "fill_blank", "match_following", "sequence", "assertion_reason",
    "short_answer", "analytical", "scenario", "application", "numerical", "voice",
]
BLOOM_LEVELS = ["remember", "understand", "apply", "analyze", "evaluate", "create"]
DIFFICULTIES = ["easy", "medium", "hard"]

DEFAULT_MARKS = {
    "mcq": 1, "true_false": 1, "fill_blank": 1, "match_following": 4, "sequence": 3, "assertion_reason": 2,
    "short_answer": 3, "analytical": 5, "scenario": 4, "application": 4, "numerical": 3, "voice": 5,
}
# types that are graded by comparing with an exact key
OBJECTIVE_TYPES = {"mcq", "true_false", "assertion_reason", "fill_blank", "numerical", "match_following", "sequence"}
# types graded against a rubric (optionally with an LLM)
RUBRIC_TYPES = {"short_answer", "analytical", "scenario", "application", "voice"}
ASSERTION_REASON_OPTIONS = [
    "Both (A) and (R) are true, and (R) is the correct explanation of (A)",
    "Both (A) and (R) are true, but (R) is not the correct explanation of (A)",
    "(A) is true, but (R) is false",
    "(A) is false, but (R) is true",
]
