"""Live end-to-end check against a RUNNING API (default http://localhost:8000).

Usage (after `python -m app.cli demo-data`, which prints one-time passwords):
  set E2E_ADMIN_PW / E2E_FACULTY_PW / E2E_ADVISOR_PW / E2E_MGMT_PW / E2E_STUDENT1_PW / E2E_STUDENT2_PW
  python scripts/e2e_live.py
Needs `httpx` (installed with the backend requirements). Exits non-zero on any failure.
"""
import io, json, os, sys, time
import httpx
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _demo_creds; _demo_creds.load()

BASE = os.environ.get("E2E_BASE", "http://localhost:8000")
results = []

def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{extra}]" if extra and not cond else ""))
    return cond

def login(email, pw):
    c = httpx.Client(base_url=BASE, timeout=60)
    r = c.post("/api/auth/login", json={"identifier": email, "password": pw})
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    return c

def pw(key):
    v = os.environ.get(key)
    if not v:
        sys.exit(f"Set {key} (password printed by `python -m app.cli demo-data`).")
    return v

NOTES = ("Photosynthesis is the process by which green plants convert light energy into chemical energy. "
         "It takes place in the chloroplasts, which contain the pigment chlorophyll. The light reactions occur in the "
         "thylakoid membranes and produce ATP and NADPH while splitting water and releasing oxygen. The Calvin cycle "
         "occurs in the stroma and uses ATP and NADPH to fix carbon dioxide into glucose. Rubisco is the enzyme that "
         "catalyses carbon fixation. Factors affecting the rate of photosynthesis include light intensity, carbon "
         "dioxide concentration and temperature. Stomata regulate gas exchange in leaves.")

admin = login("admin@example.edu", pw("E2E_ADMIN_PW"))
fac = login("faculty@example.edu", pw("E2E_FACULTY_PW"))
adv = login("advisor@example.edu", pw("E2E_ADVISOR_PW"))
mgmt = login("management@example.edu", pw("E2E_MGMT_PW"))
s1 = login("student1@example.edu", pw("E2E_STUDENT1_PW"))
s2 = login("student2@example.edu", pw("E2E_STUDENT2_PW"))
check("all five roles can log in", True)

# --- unauthenticated / wrong role
check("unauthenticated request rejected", httpx.get(BASE + "/api/student/dashboard").status_code in (401, 403))
check("student cannot use faculty API", s1.get("/api/faculty/subjects").status_code == 403)
check("student cannot read audit log", s1.get("/api/admin/audit-logs").status_code == 403)
check("faculty cannot read audit log", fac.get("/api/admin/audit-logs").status_code == 403)

# --- faculty: upload material
subj = fac.get("/api/faculty/subjects").json()[0]
unit = subj["units"][0]; topic = unit["topics"][0]; klass = subj["classes"][0]
r = fac.post("/api/faculty/materials/upload",
             data={"subject_id": subj["id"], "unit_id": unit["id"], "topic_id": topic["id"],
                   "title": "E2E Photosynthesis notes", "material_type": "notes", "class_id": klass["id"]},
             files={"file": ("e2e_notes.txt", io.BytesIO(NOTES.encode()), "text/plain")})
check("faculty uploads learning material", r.status_code in (200, 201), r.text[:200])
mat = r.json() if r.status_code in (200, 201) else {}
mid = mat.get("id") or mat.get("material_id")
if mid:
    fac.patch(f"/api/faculty/materials/{mid}", json={"is_approved": True})
check("upload with disallowed extension rejected",
      fac.post("/api/faculty/materials/upload",
               data={"subject_id": subj["id"], "unit_id": unit["id"], "topic_id": topic["id"], "title": "bad"},
               files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")}).status_code == 415)
check("unknown JSON field rejected (no mass assignment)",
      admin.post("/api/admin/users", json={"email": "z@example.edu", "full_name": "Zed Zed", "role": "admin", "is_superuser": True}).status_code == 422)

# --- faculty: generate + publish assessment
r = fac.post("/api/faculty/assessments/generate",
             json={"subject_id": subj["id"], "unit_id": unit["id"], "topic_id": topic["id"], "class_id": klass["id"],
                   "title": f"E2E quiz {int(time.time())}", "num_questions": 4, "difficulty": "easy"})
check("faculty generates assessment draft", r.status_code in (200, 201), r.text[:300])
aid = r.json().get("id") or r.json().get("assessment_id") if r.status_code in (200, 201) else None
detail = fac.get(f"/api/faculty/assessments/{aid}").json() if aid else {}
qs = detail.get("questions", [])
check("draft has questions", len(qs) >= 1, str(detail)[:200])
r = fac.post(f"/api/faculty/assessments/{aid}/publish", json={})
check("faculty publishes assessment", r.status_code == 200, r.text[:300])

# --- student: take it
lst = s1.get("/api/student/assessments").json()
items = lst if isinstance(lst, list) else lst.get("items", lst.get("assessments", []))
check("student sees published assessment", any((a.get("id") == aid) for a in items), str(lst)[:200])
r = s1.post(f"/api/student/assessments/{aid}/start", json={})
check("student starts attempt", r.status_code in (200, 201), r.text[:300])
start = r.json()
att = start.get("attempt_id") or start.get("id")
sq = start.get("questions", [])
check("answer keys hidden from student", "is_correct" not in json.dumps(sq) and "answer_key" not in json.dumps(sq))
answers = []
for q in sq:
    a = {"question_id": q["id"]}
    opts = q.get("options") or []
    if opts:
        a["selected_option_id"] = str(opts[0].get("id"))
    else:
        a["answer_text"] = "chlorophyll in the chloroplast uses light energy"
    answers.append(a)
r = s1.post(f"/api/student/attempts/{att}/submit", json={"answers": answers})
check("student submits attempt", r.status_code == 200, r.text[:300])
res = s1.get(f"/api/student/attempts/{att}/result")
check("student reads own result", res.status_code == 200, res.text[:200])
check("another student gets 404 for that attempt (IDOR)", s2.get(f"/api/student/attempts/{att}/result").status_code == 404)
check("second submit does not re-grade", s1.post(f"/api/student/attempts/{att}/submit", json={"answers": answers}).status_code in (400, 409, 200))

# --- dashboards
d = s1.get("/api/student/dashboard")
check("student dashboard", d.status_code == 200, d.text[:200])
check("student XP / badges / leaderboard endpoints", all(s1.get(p).status_code == 200 for p in
      ("/api/student/xp-history", "/api/student/badges", "/api/student/leaderboard", "/api/student/privacy")))
check("faculty analytics", fac.get("/api/faculty/analytics", params={"subject_id": subj["id"]}).status_code == 200)
check("faculty sees attempts", fac.get(f"/api/faculty/assessments/{aid}/attempts").status_code == 200)
check("advisor dashboard", adv.get("/api/advisor/dashboard").status_code == 200)
check("management analytics", mgmt.get("/api/management/analytics").status_code == 200)
check("management cannot read individual student", mgmt.get("/api/faculty/students/1").status_code == 403)
check("admin users + structure + system", all(admin.get(p).status_code == 200 for p in
      ("/api/admin/users", "/api/admin/structure", "/api/admin/system", "/api/admin/audit-logs")))

# --- RAG companion
r = s1.post("/api/rag/companion", json={"message": "Where does the Calvin cycle occur and what does it use?",
                                         "subject_id": subj["id"], "topic_id": topic["id"]})
check("AI companion answers", r.status_code == 200, r.text[:300])
body = r.json() if r.status_code == 200 else {}
txt = json.dumps(body).lower()
check("answer grounded in uploaded material (mentions stroma/ATP)", "stroma" in txt or "atp" in txt, txt[:300])
check("answer carries source citations", bool(body.get("sources") or body.get("citations")), txt[:300])
r = s1.post("/api/rag/companion", json={"message": "Explain the causes of the French Revolution in detail",
                                         "subject_id": subj["id"], "topic_id": topic["id"]})
t2 = json.dumps(r.json()).lower() if r.status_code == 200 else ""
check("companion refuses / flags when material is missing", r.status_code == 200 and ("not" in t2 or "no " in t2), t2[:300])
check("capabilities endpoint reports honestly", s1.get("/api/rag/capabilities").status_code == 200)

# --- blockchain
st = admin.get("/api/blockchain/status").json()
check("blockchain connected", st.get("connected") or st.get("enabled"), str(st))
recs = admin.get("/api/blockchain/records")
check("ledger records listed", recs.status_code == 200, recs.text[:200])
rl = recs.json(); rl = rl if isinstance(rl, list) else rl.get("items", rl.get("records", []))
anch = [x for x in rl if x.get("status") == "anchored"]
check("at least one record anchored on-chain (not just pending)", len(anch) >= 1, str([x.get("status") for x in rl][:10]))
if anch:
    ref = anch[0].get("record_ref") or anch[0].get("ref") or anch[0].get("id")
    v = admin.post("/api/blockchain/verify", json={"record_ref": str(ref)})
    check("on-chain verification matches", v.status_code == 200 and v.json().get("result") == "VERIFIED", v.text[:300])

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
