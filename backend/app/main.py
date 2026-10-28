import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (admin_routes, advisor_routes, auth_routes, blockchain_routes, faculty_assess_routes, faculty_routes,
                     management_routes, public_routes, rag_routes, student_routes)
from app.config import settings
from app.migrations import ensure_schema
from app.security.middleware import SecurityHeadersMiddleware
from app.spa import mount_frontend

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_for_startup()
    ensure_schema()
    from app.database import SessionLocal
    from app.gamification.engine import ensure_badges
    with SessionLocal() as db:
        ensure_badges(db)
    yield


app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION, lifespan=lifespan,
              docs_url=None if settings.is_production else "/docs",
              redoc_url=None, openapi_url=None if settings.is_production else "/openapi.json")
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Authorization", "Content-Type"])

P = settings.API_V1_STR
for r in (auth_routes, admin_routes, faculty_routes, faculty_assess_routes, student_routes, advisor_routes,
          management_routes, rag_routes, blockchain_routes, public_routes):
    app.include_router(r.router, prefix=P)


@app.exception_handler(RequestValidationError)
async def _validation(request: Request, exc: RequestValidationError):
    errs = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Invalid request.", "errors": errs})


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)          # details stay in the server log only
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on the server."})


@app.get(f"{P}/health")
def health():
    return {"status": "ok", "version": settings.VERSION, "auth_mode": settings.AUTH_MODE}


# Single-port mode: if the front-end has been built, serve it (must be registered last: it is a catch-all).
if not (settings.SERVE_FRONTEND and mount_frontend(app, settings.FRONTEND_DIST, P)):
    @app.get("/")
    def root():
        return {"platform": settings.PROJECT_NAME, "version": settings.VERSION, "status": "online",
                "note": "API only. Build the front-end (npm run build) to serve the UI from here, or run `npm run dev`."}
