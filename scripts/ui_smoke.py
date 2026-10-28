"""Browser smoke test (Playwright/Chromium) against a running frontend (default http://127.0.0.1:5173).
Logs in as every role, visits that role's pages at desktop AND phone width, and fails on console errors,
uncaught exceptions, failed API calls (5xx), or horizontal page overflow. Screenshots go to ./ui-shots.
Env: E2E_{ADMIN,MGMT,ADVISOR,FACULTY,STUDENT1}_PW  (printed by `python -m app.cli demo-data`)
"""
import os, sys, pathlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _demo_creds; _demo_creds.load()
from playwright.sync_api import sync_playwright

BASE = os.environ.get("UI_BASE", "http://localhost:8000")   # single-port mode; use http://127.0.0.1:5173 for `npm run dev`
OUT = pathlib.Path(os.environ.get("UI_SHOTS", "ui-shots")); OUT.mkdir(exist_ok=True)
ROLES = {
    "student": ("student1@example.edu", "E2E_STUDENT1_PW", ["/app", "/app/assessments", "/app/companion", "/app/materials", "/app/rewards", "/app/ledger"]),
    "faculty": ("faculty@example.edu", "E2E_FACULTY_PW", ["/app", "/app/curriculum", "/app/studio", "/app/review", "/app/ledger"]),
    "advisor": ("advisor@example.edu", "E2E_ADVISOR_PW", ["/app", "/app/ledger"]),
    "management": ("management@example.edu", "E2E_MGMT_PW", ["/app"]),
    "admin": ("admin@example.edu", "E2E_ADMIN_PW", ["/app", "/app/ledger"]),
}
VIEWPORTS = {"desktop": (1366, 800), "mobile": (390, 844)}
problems = []

with sync_playwright() as p:
    b = p.chromium.launch()
    for role, (email, pwenv, pages) in ROLES.items():
        pw = os.environ.get(pwenv) or sys.exit(f"Set {pwenv}")
        ctx = b.new_context(viewport={"width": 1366, "height": 800})
        pg = ctx.new_page()
        cur = {"where": "login"}
        pg.on("console", lambda m: m.type == "error" and "ERR_TUNNEL" not in m.text and "401" not in m.text and problems.append((role, cur["where"], "console", m.text[:200])))
        pg.on("pageerror", lambda e: problems.append((role, cur["where"], "pageerror", str(e)[:200])))
        pg.on("response", lambda r: r.url.startswith(BASE + "/api") and r.status >= 500 and problems.append((role, cur["where"], "http", f"{r.status} {r.url}")))
        pg.goto(BASE + "/login"); pg.fill("#id", email); pg.fill("#pw", pw); pg.click("button.btn.accent")
        pg.wait_for_url("**/app**", timeout=20000)
        for vp, (w, h) in VIEWPORTS.items():
            pg.set_viewport_size({"width": w, "height": h})
            for path in pages:
                cur["where"] = f"{vp}:{path}"
                pg.goto(BASE + path); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(700)
                over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                if over > 2: problems.append((role, cur["where"], "overflow", f"{over}px horizontal overflow"))
                if "/login" in pg.url: problems.append((role, cur["where"], "redirect", "bounced to login"))
                pg.screenshot(path=str(OUT / f"{role}{path.replace('/', '_')}_{vp}.png"), full_page=True)
                print("ok", role, vp, path)
        if role == "student":
            cur["where"] = "companion-ask"; pg.set_viewport_size({"width": 1366, "height": 800}); pg.goto(BASE + "/app/companion")
            pg.wait_for_load_state("networkidle")
            box = pg.locator("input[aria-label='Your question']")
            box.fill("What does the Calvin cycle do?"); box.press("Enter"); pg.wait_for_timeout(2500)
            print("companion body contains answer:", "calvin" in pg.inner_text("body").lower())
        ctx.close()
    b.close()

print(f"\n{len(problems)} problem(s)")
for x in problems: print("  ", x)
sys.exit(1 if problems else 0)
