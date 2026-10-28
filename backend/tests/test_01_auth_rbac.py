"""Auth, RBAC, IDOR (required tests 1-7, 16)."""
import datetime
import jwt
from app.config import settings
from tests.conftest import PW, login


def test_unauthenticated_and_invalid_tokens_rejected(client, world):
    assert client.get("/api/student/dashboard").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    expired = jwt.encode({"sub": "local-s1@t.edu", "iat": 1, "exp": 2, "email": "s1@t.edu"},
                         settings.local_auth_secret(), algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + expired}).status_code == 401
    wrong_key = jwt.encode({"sub": "local-s1@t.edu", "iat": 1, "exp": 9999999999}, "x" * 40, algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer " + wrong_key}).status_code == 401


def test_login_generic_errors_and_student_id_login(client, world):
    r1 = client.post("/api/auth/login", json={"identifier": "nobody@t.edu", "password": "xxxxxxxxxx"})
    r2 = client.post("/api/auth/login", json={"identifier": "s1@t.edu", "password": "wrong-password-1"})
    assert r1.status_code == r2.status_code == 401 and r1.json() == r2.json()
    r = client.post("/api/auth/login", json={"identifier": "R001", "password": PW})     # student-ID login
    assert r.status_code == 200 and r.json()["user"]["role"] == "student"
    assert "hashed_password" not in r.text and "firebase_uid" not in r.text


def test_lockout_after_repeated_failures(client, world):
    for _ in range(settings.LOGIN_MAX_FAILURES):
        client.post("/api/auth/login", json={"identifier": "s2@t.edu", "password": "wrong-password-1"})
    r = client.post("/api/auth/login", json={"identifier": "s2@t.edu", "password": PW})
    assert r.status_code == 429


def test_role_self_escalation_blocked(client, world, H):
    h = H("s1@t.edu")
    assert client.patch(f"/api/admin/users/{world['admin']}", headers=h, json={"role": "admin"}).status_code == 403
    assert client.post("/api/admin/users", headers=h, json={"email": "x@t.edu", "full_name": "X Y", "role": "admin"}).status_code == 403
    # mass assignment: unknown fields are rejected, not silently applied
    r = client.patch("/api/student/privacy", headers=h, json={"leaderboard_opt_in": False, "role": "admin"})
    assert r.status_code == 422
    assert client.get("/api/auth/me", headers=h).json()["role"] == "student"


def test_admin_only_endpoints_and_audit_log_access(client, world, H):
    for who in ("s1@t.edu", "fac1@t.edu", "adv1@t.edu", "mgmt@t.edu"):
        assert client.get("/api/admin/audit-logs", headers=H(who)).status_code == 403, who
    r = client.get("/api/admin/audit-logs", headers=H("admin@t.edu"))
    assert r.status_code == 200 and any(x["action"] == "LOGIN_SUCCESS" for x in r.json())
    assert PW not in r.text


def test_deactivated_user_and_logout_revocation(client, world, H):
    h = H("s1@t.edu")
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401           # revoked immediately
    uid = client.get("/api/admin/users", headers=H("admin@t.edu")).json()
    s2 = next(u for u in uid if u["email"] == "s2@t.edu")
    h2 = H("s2@t.edu")
    assert client.patch(f"/api/admin/users/{s2['id']}", headers=H("admin@t.edu"), json={"is_active": False}).status_code == 200
    assert client.get("/api/auth/me", headers=h2).status_code in (401, 403)
    assert client.post("/api/auth/login", json={"identifier": "s2@t.edu", "password": PW}).status_code == 403


def test_cross_student_access_blocked(client, world, H, published):
    h1, h3 = H("s1@t.edu"), H("s3@t.edu")
    r = client.post(f"/api/student/assessments/{published['id']}/start", headers=h1, json={})
    assert r.status_code == 200
    att = r.json()["attempt_id"]
    # student 2 (same class) and student 3 (other class) cannot touch student 1's attempt
    for who in ("s2@t.edu", "s3@t.edu"):
        h = H(who)
        assert client.get(f"/api/student/attempts/{att}/result", headers=h).status_code == 404
        assert client.put(f"/api/student/attempts/{att}/answer", headers=h, json={"question_id": 1, "answer_text": "x"}).status_code == 404
        assert client.post(f"/api/student/attempts/{att}/submit", headers=h, json={"answers": []}).status_code == 404
    # other-class student cannot even see / start the class-A assessment
    assert client.post(f"/api/student/assessments/{published['id']}/start", headers=h3, json={}).status_code == 404
    assert published["id"] not in [a["id"] for a in client.get("/api/student/assessments", headers=h3).json()]


def test_faculty_subject_isolation(client, world, H, published):
    f2 = H("fac2@t.edu")
    assert client.get(f"/api/faculty/assessments/{published['id']}", headers=f2).status_code == 403
    assert client.get(f"/api/faculty/materials?subject_id={world['bio']}", headers=f2).status_code == 403
    assert client.get(f"/api/faculty/analytics?subject_id={world['bio']}", headers=f2).status_code == 403
    r = client.post("/api/faculty/materials/upload", headers=f2, data={
        "subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"], "title": "Evil"},
        files={"file": ("n.txt", b"hello world content", "text/plain")})
    assert r.status_code == 403
    assert client.post("/api/faculty/assessments/generate", headers=f2, json={
        "subject_id": world["bio"], "unit_id": world["u1"], "topic_id": world["t1"], "title": "Nope"}).status_code == 403
    # students cannot call faculty endpoints at all
    assert client.get("/api/faculty/subjects", headers=H("s1@t.edu")).status_code == 403


def test_advisor_sees_only_assigned_students(client, world, H):
    d = client.get("/api/advisor/dashboard", headers=H("adv1@t.edu")).json()
    ids = {s["student_id"] for s in d["students"]}
    assert ids == {world["s1"], world["s2"]}
    assert client.get(f"/api/advisor/students/{world['s3']}", headers=H("adv1@t.edu")).status_code == 403
    assert client.get(f"/api/advisor/students/{world['s1']}", headers=H("adv2@t.edu")).status_code == 403
    assert client.get(f"/api/advisor/students/{world['s1']}", headers=H("adv1@t.edu")).status_code == 200
    r = client.post("/api/advisor/reviews", headers=H("adv2@t.edu"), json={
        "student_id": world["s1"], "observation": "obs", "recommendation": "rec"})
    assert r.status_code == 403
    assert client.get("/api/advisor/dashboard", headers=H("s1@t.edu")).status_code == 403


def test_management_gets_aggregates_only(client, world, H):
    h = H("mgmt@t.edu")
    r = client.get("/api/management/analytics", headers=h)
    assert r.status_code == 200
    body = r.text
    assert "s1@t.edu" not in body and "R001" not in body and "Student 1" not in body
    assert client.get(f"/api/advisor/students/{world['s1']}", headers=h).status_code == 403
    assert client.get(f"/api/faculty/attempts/1", headers=h).status_code == 403
    assert client.get("/api/admin/users", headers=h).status_code == 403
    # small groups are suppressed
    assert all(d.get("suppressed") for d in r.json()["departments"]) or r.json()["departments"] == []


def test_security_headers_and_error_hiding(client, world):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    r = client.post("/api/auth/login", json={"identifier": "a", "password": ""})
    assert r.status_code == 422 and "Traceback" not in r.text
