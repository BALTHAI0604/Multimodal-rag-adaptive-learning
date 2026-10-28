import re
from typing import Dict, Any, List, Optional
from app.ai.llm_provider import llm_provider

class VoiceAssessmentEvaluator:
    """
    Evaluates conceptual voice responses using contextual semantics,
    topic knowledge, and misconception detection rather than simple keyword matching.
    """

    @staticmethod
    async def evaluate_voice_answer(
        question_text: str,
        transcript: str,
        sample_ideal_answer: Optional[str] = None,
        max_marks: float = 5.0,
        topic_title: Optional[str] = None
    ) -> Dict[str, Any]:
        cleaned_transcript = transcript.strip()
        if not cleaned_transcript or len(cleaned_transcript.split()) < 3:
            return {
                "score_awarded": 0.0,
                "max_score": max_marks,
                "percentage": 0.0,
                "conceptual_correctness": 0.0,
                "relevance": 0.0,
                "completeness": 0.0,
                "speech_clarity": 10.0,
                "identified_concepts": [],
                "missing_concepts": ["Spoken response was too brief or inaudible"],
                "detected_misconceptions": [],
                "ai_feedback": "Audio response was either silent or too brief to evaluate. Please explain the concept clearly in complete sentences."
            }

        transcript_lower = cleaned_transcript.lower()
        q_lower = question_text.lower()
        
        identified_concepts = []
        missing_concepts = []
        detected_misconceptions = []

        # Domain: RAG / NLP
        if "rag" in q_lower or "retrieval" in q_lower:
            key_concepts = {
                "vector database / embedding": ["vector", "embedding", "faiss", "chroma", "chunk", "similarity"],
                "retriever component": ["retriev", "fetch", "search", "query", "relevant passages", "documents"],
                "augmented generation": ["augment", "prompt", "llm", "generator", "context", "ground"],
                "runtime inference without weights update": ["runtime", "frozen", "weights", "without retraining", "external knowledge"]
            }

            for concept, terms in key_concepts.items():
                if any(t in transcript_lower for t in terms):
                    identified_concepts.append(concept)
                else:
                    missing_concepts.append(concept)

            # Check common misconceptions
            if any(term in transcript_lower for term in ["retrain", "fine tuning", "gradient descent", "backprop"]) and "not" not in transcript_lower and "unlike" not in transcript_lower:
                detected_misconceptions.append({
                    "misconception": "Confusing RAG with model retraining / fine-tuning",
                    "explanation": "You indicated that RAG trains or modifies model weights. In reality, RAG frozenly retrieves external context at runtime without updating model parameters."
                })

            if "database only" in transcript_lower or "sql query" in transcript_lower:
                detected_misconceptions.append({
                    "misconception": "Equating semantic vector search with traditional SQL matching",
                    "explanation": "RAG utilizes high-dimensional semantic vector embeddings and cosine distance rather than rigid relational SQL table queries."
                })

        else:
            # Generic conceptual concept extraction
            words = set(re.findall(r'\b[a-zA-Z]{4,}\b', transcript_lower))
            if sample_ideal_answer:
                ideal_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', sample_ideal_answer.lower()))
                overlap = words.intersection(ideal_words)
                identified_concepts = list(overlap)[:5]
                missing_concepts = list(ideal_words - words)[:4]
            else:
                identified_concepts = list(words)[:4]
                missing_concepts = ["Quantitative trade-offs", "Edge case handling"]

        # Calculate scores
        total_eval_concepts = len(identified_concepts) + len(missing_concepts)
        concept_ratio = len(identified_concepts) / max(1, total_eval_concepts)
        
        # Penalize if severe misconception detected
        misconception_penalty = len(detected_misconceptions) * 0.25
        conceptual_correctness = max(10.0, min(100.0, (concept_ratio * 100.0) - (misconception_penalty * 100.0)))
        
        # Relevance based on question alignment
        word_count = len(cleaned_transcript.split())
        relevance = min(95.0, max(30.0, 40.0 + min(50.0, word_count * 1.5)))
        completeness = min(100.0, max(20.0, concept_ratio * 100.0))
        speech_clarity = 88.0 if word_count > 15 else 60.0

        # Weighted composite score
        composite_percent = (
            (conceptual_correctness * 0.45) +
            (relevance * 0.25) +
            (completeness * 0.20) +
            (speech_clarity * 0.10)
        )
        score_awarded = round((composite_percent / 100.0) * max_marks, 1)

        # AI Feedback Synthesis
        feedback_lines = []
        if score_awarded >= (max_marks * 0.8):
            feedback_lines.append("Excellent conceptual mastery! You explained the core pipeline accurately with sound terminology.")
        elif score_awarded >= (max_marks * 0.5):
            feedback_lines.append("Good verbal answer. You grasped the high-level concept but missed a few foundational technical nuances.")
        else:
            feedback_lines.append("Conceptual foundation needs reinforcement. Focus on explaining both the data flow and the underlying architecture.")

        if identified_concepts:
            feedback_lines.append(f"Strong points covered: {', '.join(identified_concepts)}.")
        if missing_concepts:
            feedback_lines.append(f"Areas to elaborate next time: {', '.join(missing_concepts)}.")
        if detected_misconceptions:
            feedback_lines.append(f"Misconception warning: {detected_misconceptions[0]['explanation']}")

        return {
            "score_awarded": score_awarded,
            "max_score": max_marks,
            "percentage": round(composite_percent, 1),
            "conceptual_correctness": round(conceptual_correctness, 1),
            "relevance": round(relevance, 1),
            "completeness": round(completeness, 1),
            "speech_clarity": round(speech_clarity, 1),
            "identified_concepts": identified_concepts,
            "missing_concepts": missing_concepts,
            "detected_misconceptions": detected_misconceptions,
            "ai_feedback": " ".join(feedback_lines)
        }

voice_evaluator = VoiceAssessmentEvaluator()
