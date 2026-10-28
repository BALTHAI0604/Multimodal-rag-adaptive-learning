"""Serve the built React app (frontend/dist) from the API so the whole platform runs on ONE port.

Only active when the build exists (npm run build). API routes (/api/*) always win; unknown /api paths
return a JSON 404, never the HTML shell. Path traversal is rejected.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse


def mount_frontend(app: FastAPI, dist: Path, api_prefix: str = "/api") -> bool:
    dist = Path(dist).resolve()
    index = dist / "index.html"
    if not index.is_file():
        return False
    prefix = api_prefix.strip("/")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path == prefix or full_path.startswith(prefix + "/"):
            return JSONResponse(status_code=404, content={"detail": "Not found."})
        if full_path:
            target = (dist / full_path).resolve()
            if target.is_file() and dist in target.parents:
                return FileResponse(target)
            if "." in Path(full_path).name:                      # a missing asset must 404, not return HTML
                raise HTTPException(404, "Not found.")
        return FileResponse(index, media_type="text/html")

    return True
