"""RAG verification: grounded answers, fabricated citations, unavailable/failed model, restricted material,
privacy of prompts, and measured retrieval quality on a known corpus (lexical hashing embedder - NOT semantic)."""
import json
import os
import statistics
import time

import pytest

from app.ai.llm_provider import LLMError, LLMUnavailable, llm_provider
from app.rag import pipeline

Q = lambda world, text, **kw: {"message": text, "language": "English", "subject_id": world["bio"], **kw}


def ask(client, H, world, text, who="s1@t.edu", **kw):
    r = client.post("/api/rag/companion", headers=H(who), json=Q(world, text, **kw))
    assert r.status_code == 200, r.text
    return r.json()


class Double:
    def __init__(self, reply="", exc=None):
        self.reply, self.exc, self.calls = reply, exc, []

    def __call__(self, messages, json_mode):
        self.calls.append(messages)
        if self.exc:
            raise self.exc
        return self.reply


def test_grounded_answer_uses_model_and_labels_it(client, world, H, material):
    llm_provider.set_override(Double("Chlorophyll absorbs light in the chloroplast [1]."))
    d = ask(client, H, world, "What does chlorophyll do?")
    assert d["answer_type"] == "grounded" and d["is_rag_grounded"] and d["cited_sources"] == [1] and d["citation_warning"] is None


def test_fabricated_citations_are_removed_and_flagged(client, world, H, material):
    llm_provider.set_override(Double("Chlorophyll absorbs light [1]. It also cures colds [7][9]."))
    d = ask(client, H, world, "What does chlorophyll do?")
    assert "[7]" not in d["answer"] and "[9]" not in d["answer"] and "[1]" in d["answer"]
    assert "Removed citation" in d["citation_warning"] and d["cited_sources"] == [1]


def test_uncited_model_answer_is_flagged(client, world, H, material):
    llm_provider.set_override(Double("Chlorophyll absorbs light."))
    d = ask(client, H, world, "What does chlorophyll do?")
    assert d["answer_type"] == "grounded" and d["cited_sources"] == [] and "did not cite" in d["citation_warning"]


@pytest.mark.parametrize("double", [Double(exc=LLMUnavailable("down")), Double(exc=LLMError("timeout")), Double(reply="   ")])
def test_model_unavailable_or_failed_never_reported_as_ai_answer(client, world, H, material, double):
    llm_provider.set_override(double)
    d = ask(client, H, world, "What does chlorophyll do?")
    assert d["answer_type"] == "extractive" and d["provider"] == "extractive (no LLM)"     # clearly NOT a generated answer
    assert d["sources"] and "offline" in d["answer"].lower()


def test_real_ollama_unreachable_gives_honest_extractive_fallback(client, world, H, material):
    llm_provider.set_override(None)                                   # OLLAMA_BASE_URL points at a closed port in tests
    d = ask(client, H, world, "What does chlorophyll do?")
    assert d["answer_type"] == "extractive"
    cap = client.get("/api/rag/capabilities", headers=H("s1@t.edu")).json()
    assert cap["llm"]["available"] is False and "ollama" in cap["llm"]["error"].lower()
    assert cap["embeddings"]["semantic"] is False                      # lexical fallback is not presented as semantic


def test_absent_answer_does_not_call_the_model(client, world, H, material):
    dbl = Double("INVENTED ANSWER [1]")
    llm_provider.set_override(dbl)
    d = ask(client, H, world, "Explain the causes of the French Revolution")
    assert d["answer_type"] == "insufficient_material" and d["sources"] == [] and not dbl.calls
    assert "INVENTED" not in d["answer"]


def test_prompt_contains_only_retrieved_passages_and_no_personal_data(client, world, H, material):
    dbl = Double("Chlorophyll absorbs light [1].")
    llm_provider.set_override(dbl)
    ask(client, H, world, "What does chlorophyll do?")
    prompt = json.dumps(dbl.calls[0])
    for private in ("s1@t.edu", "Student 1", "R001", "Test-Passw0rd"):
        assert private not in prompt
    assert "Course passages" in prompt and "chlorophyll" in prompt.lower()


def test_restricted_material_never_reaches_prompt_or_sources(client, world, H, material):
    secret = "Zyxwvut protocol requires the cobalt handshake sequence."
    f = H("fac2@t.edu")                                              # chemistry faculty uploads to chemistry only
    up = client.post("/api/faculty/materials/upload", headers=f, data={
        "subject_id": world["che"], "unit_id": world["u2"], "topic_id": world["t2"], "title": "Chem secret"},
        files={"file": ("c.txt", secret.encode(), "text/plain")})
    assert up.status_code == 201
    dbl = Double("x [1]")
    llm_provider.set_override(dbl)
    d = ask(client, H, world, "cobalt handshake sequence zyxwvut protocol")            # biology-only student
    assert d["sources"] == [] and not dbl.calls
    assert client.post("/api/rag/companion", headers=H("fac1@t.edu"), json=Q(world, "cobalt handshake", subject_id=world["che"])).status_code == 403
    assert client.post("/api/rag/companion", headers=H("mgmt@t.edu"), json=Q(world, "anything")).status_code == 403


# ----------------------------------------------------------------- retrieval quality on a known corpus
CORPUS = {
    "Cell division": "Mitosis is the process of cell division that produces two identical daughter cells. The stages of mitosis are prophase, metaphase, anaphase and telophase. During metaphase the chromosomes line up at the cell equator. Meiosis produces four genetically different gametes.",
    "Newton laws": "Newton's first law states that an object stays at rest or in uniform motion unless acted on by a force. The second law says force equals mass times acceleration. The third law states that every action has an equal and opposite reaction. Inertia is the resistance of an object to changes in motion.",
    "Python lists": "A Python list is an ordered mutable collection written with square brackets. Use append to add an item and pop to remove one. List comprehensions build new lists from iterables. Tuples are immutable sequences, unlike lists.",
    "Water cycle": "The water cycle describes how water moves between the oceans, atmosphere and land. Evaporation turns liquid water into vapour, condensation forms clouds, and precipitation returns water as rain or snow. Runoff carries water back to rivers and the sea.",
    "Photosynthesis": "Photosynthesis is the process by which plants make glucose from carbon dioxide and water using light energy. Chlorophyll absorbs light in the chloroplast. The Calvin cycle occurs in the stroma and fixes carbon dioxide using the enzyme RuBisCO with ATP and NADPH.",
}
ANSWERABLE = [
    ("What are the stages of mitosis?", "Cell division"), ("What happens during metaphase?", "Cell division"),
    ("State Newton's second law", "Newton laws"), ("What is inertia?", "Newton laws"),
    ("How do I add an item to a Python list?", "Python lists"), ("What is the difference between tuples and lists?", "Python lists"),
    ("How does evaporation and condensation form clouds?", "Water cycle"), ("What is runoff?", "Water cycle"),
    ("Where does the Calvin cycle occur?", "Photosynthesis"), ("Which enzyme fixes carbon dioxide?", "Photosynthesis"),
]
UNANSWERABLE = ["Who won the football world cup in 1998?", "Explain the causes of the French Revolution",
                "What is the capital of Australia?", "How do I bake sourdough bread?"]


def test_retrieval_quality_metrics_on_known_corpus(client, world, H):
    f = H("fac1@t.edu")
    for title, text in CORPUS.items():
        r = client.post("/api/faculty/materials/upload", headers=f, data={
            "subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"], "title": title},
            files={"file": (title.replace(" ", "_") + ".txt", text.encode(), "text/plain")})
        assert r.status_code == 201 and r.json()["status"] == "indexed"
    ranks, lat = [], []
    for q, expected in ANSWERABLE:
        t0 = time.perf_counter()
        d = ask(client, H, world, q)
        lat.append((time.perf_counter() - t0) * 1000)
        titles = [s["title"] for s in d["sources"]]
        ranks.append(titles.index(expected) + 1 if expected in titles else None)
    n = len(ranks)
    recall1 = sum(r == 1 for r in ranks) / n
    recall3 = sum(r is not None and r <= 3 for r in ranks) / n
    mrr = sum(1 / r for r in ranks if r) / n
    fp_detail = {q: [(x["title"], x["relevance"]) for x in ask(client, H, world, q)["sources"]] for q in UNANSWERABLE}
    false_pos = sum(bool(v) for v in fp_detail.values()) / len(UNANSWERABLE)
    min_correct = min(min(x["relevance"] for x in ask(client, H, world, q)["sources"] if x["title"] == e) for q, e in ANSWERABLE)
    metrics = {"embedder": "hashing-lexical (not semantic)", "documents": len(CORPUS), "answerable_queries": n,
               "recall_at_1": round(recall1, 3), "recall_at_3": round(recall3, 3), "mrr": round(mrr, 3),
               "false_positive_rate_on_unanswerable": round(false_pos, 3), "unanswerable_queries": len(UNANSWERABLE),
               "latency_ms_mean": round(statistics.mean(lat), 1), "latency_ms_p95": round(sorted(lat)[int(0.95 * (n - 1))], 1),
               "per_query_rank": ranks, "false_positive_detail": fp_detail,
               "lowest_relevance_of_a_correct_hit": round(min_correct, 3)}
    print("\nRAG_METRICS", json.dumps(metrics))
    if os.environ.get("RAG_EVAL_OUT"):
        open(os.environ["RAG_EVAL_OUT"], "w").write(json.dumps(metrics, indent=2))
    # regression floors (lexical retrieval on this corpus); they are not a claim of semantic quality
    assert recall3 >= 0.7 and mrr >= 0.6
    assert false_pos == 0


def test_chunks_from_an_older_embedder_are_reembedded_not_lost(client, world, H, material):
    """Existing databases hold 1024-d vectors from hashing-lexical-v1; they must be transparently re-embedded."""
    import numpy as np
    from app.database import SessionLocal
    from app.models.models import DocumentChunk
    from app.rag.vector_store import index
    db = SessionLocal()
    for c in db.query(DocumentChunk):
        c.embedding, c.embedding_model = np.zeros(1024, dtype=np.float32).tobytes(), "hashing-lexical-v1"
    db.commit()
    db.close()
    index.loaded = False
    d = ask(client, H, world, "Where does the Calvin cycle occur?")
    assert d["sources"] and d["embedding_backend"] == "hashing-lexical-v2"
    db = SessionLocal()
    assert {c.embedding_model for c in db.query(DocumentChunk)} == {"hashing-lexical-v2"}
    db.close()
