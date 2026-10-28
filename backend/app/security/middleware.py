import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds request ids and defensive headers to every response."""

    async def dispatch(self, request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:16]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(self), geolocation=()"
        path = request.url.path
        is_api = path.startswith("/api")
        if path.startswith("/assets/"):                       # content-hashed build files
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-store"
        if path.startswith(("/docs", "/redoc", "/openapi.json")):
            return response
        if is_api:
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        else:                                                  # the single-page app: same-origin only, no third parties
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
                "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; manifest-src 'self'; "
                "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        return response
