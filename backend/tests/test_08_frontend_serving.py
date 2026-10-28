"""Single-port mode: SPA fallback, asset serving, API 404s stay JSON, traversal blocked, CSP split."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.middleware import SecurityHeadersMiddleware
from app.spa import mount_frontend


def _app(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>SPA</title>")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/api/ping")
    def ping():
        return {"ok": True}
    assert mount_frontend(app, dist) is True
    return TestClient(app)


def test_spa_fallback_assets_and_api(tmp_path):
    c = _app(tmp_path)
    assert "SPA" in c.get("/").text and "SPA" in c.get("/app/companion").text and "SPA" in c.get("/login").text
    assert c.get("/assets/app-abc123.js").text == "console.log(1)"
    assert c.get("/api/ping").json() == {"ok": True}
    r = c.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json() == {"detail": "Not found."}          # JSON, not the HTML shell
    assert c.get("/assets/missing.js").status_code == 404                           # missing asset is not index.html


def test_path_traversal_is_blocked(tmp_path):
    c = _app(tmp_path)
    for p in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/../../secret.txt"):
        r = c.get(p)
        assert "TOP SECRET" not in r.text, p


def test_headers_differ_for_app_and_api_and_assets_are_cacheable(tmp_path):
    c = _app(tmp_path)
    page, api, asset = c.get("/app"), c.get("/api/ping"), c.get("/assets/app-abc123.js")
    assert "script-src 'self'" in page.headers["content-security-policy"] and "connect-src 'self'" in page.headers["content-security-policy"]
    assert api.headers["content-security-policy"].startswith("default-src 'none'")
    assert page.headers["cache-control"] == "no-store" and "immutable" in asset.headers["cache-control"]
    assert page.headers["x-frame-options"] == "DENY" and page.headers["x-content-type-options"] == "nosniff"


def test_no_build_means_no_mount(tmp_path):
    assert mount_frontend(FastAPI(), tmp_path / "missing") is False
