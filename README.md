<<<<<<< HEAD
# Multimodal RAG-Driven Adaptive Learning with Blockchain-Secured Student Intelligence

A working learning platform: a course-grounded AI companion (RAG with citations), adaptive practice, assessments with
server-side integrity, gamification, role dashboards (Student · Faculty · Advisor · Management · Admin) and a tamper-evident
ledger (hashes of grade records anchored on a Solidity contract). FastAPI + SQLite backend, React/Vite UI, Hardhat chain.
Everything is free and runs locally.

## 1. Run it (3 steps)

### Windows
1. Install **Python 3.10+** (tick *Add to PATH*) and **Node.js 20+**.
2. Double-click **`Setup (first time).bat`** (installs everything, builds the UI, creates sample accounts → `data\demo-accounts.txt`).
3. Double-click **`Start Platform.bat`** → opens **http://localhost:8000** (UI + API + local blockchain on one port).

Or in PowerShell: `.\scripts\setup.ps1 -Demo` then `.\scripts\start.ps1` (if blocked: `Set-ExecutionPolicy -Scope Process Bypass`).

### macOS / Linux
```bash
./scripts/setup.sh --demo      # add --ai for neural embeddings + Whisper (large downloads)
./scripts/start.sh             # http://localhost:8000   (--no-chain to skip the blockchain)
```
Sign in with the accounts in `data/demo-accounts.txt` (one-time random passwords, development only). For real use skip
`--demo` and create your admin: `cd backend && .venv\Scripts\python -m app.cli create-admin --email you@college.edu --name "You"`
(macOS/Linux: `.venv/bin/python`).

### Developer mode (hot reload)
`cd backend && .venv\Scripts\python -m uvicorn app.main:app --reload --port 8000` and in another terminal
`cd frontend && npm run dev` → http://localhost:5173 (proxies `/api`).

### Optional AI components (all free; the app tells you honestly what is missing — Admin → System)
| Component | Install | Without it |
|---|---|---|
| **Ollama** (written answers, AI question generation) | `winget install Ollama.Ollama` → `ollama pull llama3.2` → `ollama serve`  (≈2 GB download) | Answers are verbatim cited excerpts labelled *AI model offline*; questions use extraction templates (numerical needs the LLM) |
| **Neural embeddings** (semantic search, multilingual) | `setup -AI` / `pip install -r backend/requirements-optional.txt` (model ≈0.5 GB from Hugging Face, once) | Keyword (lexical) search, labelled as such |
| **Whisper** (voice answers) | included in `-AI`; model downloads on first use | Typed answers only |
| **Piper** (server voices) | install Piper + voices, set `PIPER_*` in `.env` | Browser voices are used |
| **Tesseract** (scanned PDFs/images) | install Tesseract (+ language packs, `OCR_LANGS=eng+tam`) | Image-only files report "OCR not available"; missing language packs are named in a warning |

Test the LLM: `ollama list` shows the model, then ask the Companion — the badge changes from *Passages from your material (AI model offline)* to *Grounded in your material*.

## 2. What you get (by role)
* **Student** – Today (mastery, streak, recommended next steps with reasons) · Assessments (timed, server-enforced deadlines, voice answers) · **Companion** (cited answers, 5 languages, suggested questions, listen/copy) · Materials · Rewards (XP, badges, opt-in leaderboard) · Integrity (verify your own records).
* **Faculty** – Curriculum, material upload (PDF/DOCX/PPTX/TXT/images/audio), generate → review → publish assessments, grade overrides (versioned), integrity-event review, per-subject analytics.
* **Advisor** – only assigned students; reviews and interventions.   **Management** – aggregates only (groups < 5 hidden), CSV export.   **Admin** – users, structure, assignments, system status, audit log, ledger retry.

## 3. Authentication
* `AUTH_MODE=firebase` (required in production, server refuses to start otherwise): Firebase Auth. The API verifies ID tokens with the Admin SDK, signs in server-side, so student-ID login never exposes e-mails. Email verification and password-reset e-mails are sent by Firebase. Accounts are created only by an administrator (no self sign-up, no demo logins).
* `AUTH_MODE=local` (development/tests only): bcrypt + locally-signed tokens, no Firebase project needed.
* Free emulator: `npm i -g firebase-tools ; firebase emulators:start --only auth,firestore` and set `FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099`.
* Never commit a service-account JSON (git-ignored). Point `GOOGLE_APPLICATION_CREDENTIALS` at a file outside the repo.

### Firebase setup
1. Create a project, enable **Email/Password** sign-in. 2. Project settings → copy the **Web API key** → `FIREBASE_WEB_API_KEY`, project id → `FIREBASE_PROJECT_ID`. 3. Service accounts → generate a key, store it outside the repo, set `GOOGLE_APPLICATION_CREDENTIALS`. 4. `python -m app.cli create-admin --email ...` then use the reset link e-mailed to set a password. 5. Set `AUTH_MODE=firebase`, `ENVIRONMENT=production`, `COOKIE_SECURE=true`, `CORS_ORIGINS=https://your-site`.

### Firestore rules
`firestore.rules` denies everything except a user reading their own `users/{uid}` mirror (`role`, `active`), which only the backend writes. **The Admin SDK bypasses these rules**, so every API call is authorised in the backend (see matrix). Deploy: `firebase deploy --only firestore:rules`. Rules were written but **not run against an emulator in this environment**.

## 4. Role / permission matrix (enforced server-side; tested)
| Capability | Student | Faculty | Advisor | Management | Admin |
|---|---|---|---|---|---|
| Own dashboard, attempts, AI companion | own only | – | – | – | – |
| Curriculum, material upload, assessments | – | **assigned subjects/classes only** | – | – | – |
| Grade override, integrity review | – | assigned subjects/classes | – | – | – |
| Student detail | self | students in own subject scope | **assigned students only** | **none** (aggregates only, groups <5 hidden) | accounts, not marks |
| Create/disable users, structure, audit log | – | – | – | – | yes |
| Verify ledger records | own | in-scope students | assigned students | – | all |
Other students' attempts return 404; wrong-role/wrong-subject returns 403; unknown JSON fields are rejected (no mass assignment).

## 5. Verification — what was actually run (28 Oct 2026)
| Check | Command | Result |
|---|---|---|
| Backend tests | `cd backend && python -m pytest -q` | **77 passed** |
| Contract tests | `cd blockchain && npx hardhat test` | **4 passed** (offline solc-js; no compiler download needed) |
| Live API end-to-end (real Hardhat node, real contract) | `python scripts/e2e_live.py` | **35/35** — logins, RBAC, upload→generate→publish→take→grade, IDOR 404, companion with citations, record anchored on-chain and `VERIFIED` |
| Browser test, all 5 roles, desktop 1366px + phone 390px, strict CSP, single port | `python scripts/ui_smoke.py` | **0 problems** (no console errors, no 5xx, no horizontal overflow) |
| Clean-room install | unzip → `scripts/setup.sh --demo` → `scripts/start.sh` → both scripts above | passed |
| Frontend build / dependency audit | `npm run build`, `npm audit` | builds; **0 vulnerabilities** |
| Python dependency audit | `pip-audit -r backend/requirements.txt` | **no known vulnerabilities** |

**Blockchain failure modes (tested on an in-memory EVM *and* on the real Hardhat node):** wrong/missing contract and bytecode check · node down (`CHAIN_UNAVAILABLE`, record stays *pending*, never "protected") · local chain reset (`CHAIN_RESET`, detected instead of a false "anchored"; admin *Retry* re-anchors exactly once) · lost receipt after a mined transaction (adopts the existing anchor, **no duplicate**) · conflicting hash already on-chain (never overwritten → *failed*) · revert (*failed*, retryable) · read-back of the contract after every anchor.

**RAG (lexical embedder; 5 documents, 10 answerable + 4 unanswerable questions, `tests/test_05_rag_verification.py`):** recall@1 = recall@3 = 1.0, MRR = 1.0, **0/4 false positives** on off-topic questions, mean retrieval+answer latency ≈ 60 ms. This is a *small, easy* corpus and a keyword method — it is a regression guard, **not** a claim of semantic quality; re-measure after enabling neural embeddings. Also tested: fabricated `[n]` citations are stripped and flagged; model-down/timeout/empty output → labelled *extractive* fallback; absent answers never call the model; restricted material never reaches sources or prompts; prompts contain no personal data.

**Adaptive learning** is tested on known score scenarios (no data ≠ weak topic, weak/strong detection, trend, practice weight, recommendations update and cite DB-backed reasons). These are logic tests — **no claim of validated personalisation** is made.

### Status by feature
| Feature | Status | Evidence / what is missing |
|---|---|---|
| Auth, RBAC, isolation, upload/payload validation, audit log | **PASS** | pytest + live e2e (local auth mode) |
| Firebase Authentication (production) | **BLOCKED** | needs your Firebase project + service account; startup refuses insecure production config (tested) |
| Firestore rules | **BLOCKED** | emulator jar download blocked in the build environment; `firestore-rules-test/` is written but **not run** — run `npm install && npm test` there (needs Java) |
| Assessments, grading, grade versions, hidden answer keys, deadlines | **PASS** | pytest + live e2e |
| Gamification, dashboards = DB values | **PASS** | pytest |
| Adaptive mastery & recommendations | **PASS** (logic) | scenario tests; effectiveness not evaluated |
| RAG ingest/retrieve/cite, permission filtering, honest fallbacks | **PASS** | pytest + live e2e (lexical embedder) |
| Neural embeddings / semantic search | **BLOCKED here** | Hugging Face unreachable in build env; code path + auto re-embedding tested with the lexical model |
| Ollama answers / AI question generation | **PARTIAL** | validated with a test double and the unreachable-server path; **never run against a real Ollama here** |
| Blockchain anchoring, tamper detection, retry, reset, no duplicates | **PASS** (local chain) | pytest + live Hardhat; this is a *local development chain*, not a production ledger |
| Speech-to-text (Whisper) / Piper TTS | **BLOCKED here** | model downloads unreachable; validation, errors and file clean-up are tested, real recognition is not |
| OCR | **PASS** for English | real Tesseract in tests; other languages need their Tesseract packs (reported, not assumed) |
| Responsive UI, accessibility basics | **PASS** (Chromium) | Playwright desktop+phone; Firefox/Safari and a real microphone not tested |
| Windows scripts (`*.ps1`, `.bat`) | **PARTIAL** | bash equivalents were executed end-to-end; PowerShell could not be run in the Linux build environment |

## 6. Verify the main workflows yourself (5 minutes)
1. Sign in as **faculty** → *Curriculum*: upload a PDF/notes for a topic → status becomes *indexed*.
2. *Assessments* → generate from that topic → review questions → **Publish**.
3. Sign in as **student** → *Companion*: click a suggested question → answer with `[1]` citations and its source.
4. Student → *Assessments* → take it → *Result*; *Today* now shows mastery and a reasoned recommendation; *Rewards* shows XP.
5. *Integrity* → **Verify** → `VERIFIED` (record anchored on the local chain). Stop the chain, verify again → `CHAIN_UNAVAILABLE`.
6. **Admin** → *Audit log* shows every action; *System* lists exactly which optional AI parts are active.

## 7. Remaining issues (honest list)
* **Dependencies:** frontend and Python audits are clean. `blockchain/` dev tooling (Hardhat toolbox) reports ≈47 advisories, all in the *development* toolchain (not shipped to users, not part of the API/UI runtime); fixing them needs a breaking Hardhat major upgrade, so it was not forced.
* In-process rate limiter and login lockout → single backend worker only (use Redis/gateway limits for multi-worker).
* SQLite is for one institution / modest load; use PostgreSQL via `DATABASE_URL` for more.
* Back up `.env` (`DATA_ENCRYPTION_KEY`): losing it makes encrypted transcripts/records unreadable. `legacy_backup/` holds your original DB (old demo data) — delete when no longer needed.
* Hardhat's local accounts are public; the app refuses them in production (tested). Use a real registrar key and a real network/L2 for a production ledger.

## 8. Known limitations
* heynesh.com could not be fetched from the build environment, so the UI follows its *structure and motion language* (oversized type, numbered labels, pill nav, scroll reveals, marquee, timeline, accordion, cursor label) with my own palette/fonts — not a pixel copy.
* Without semantic embeddings the retriever is lexical (stated in the UI). Numerical question generation needs the LLM.
* Rate limiter is in-process (single worker). Camera/face monitoring is intentionally not implemented; only browser tab/window/fullscreen/paste events are logged for human review — a browser cannot reliably stop cheating or close tabs.
* `legacy_backup/` holds the original database (it contains old demo accounts/fabricated data; its pbkdf2 passwords cannot sign in). Delete it when no longer needed.
* Rotate keys if `.env` is ever shared. Demo data exists only via `python -m app.cli demo-data` and refuses production.

## 9. Layout
`backend/app/{api,auth,security,rag,ai,assessment,adaptive,gamification,blockchain,speech,models,schemas}`, `backend/tests`, `frontend/src/{pages,ui,lib}`, `blockchain/{contracts,abi,scripts,test}`, `firestore.rules`, `firebase.json`, `.env.example`.
`scripts/` holds setup/start scripts, `envset.py`, `e2e_live.py`, `ui_smoke.py`; `firestore-rules-test/` the (unrun) rules tests.
=======
# Multimodal-rag-adaptive-learning
>>>>>>> 4815d21c8b180cffa33a2eafa23a00ef78b57dc3
