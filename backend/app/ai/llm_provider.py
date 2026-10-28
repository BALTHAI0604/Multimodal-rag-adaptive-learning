"""Local LLM access through Ollama (free, runs on the student's machine).

There is deliberately NO canned-answer fallback here: if the model is not running the callers get
`LLMUnavailable` and decide what honest thing to show (e.g. verbatim excerpts with citations).
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings


class LLMUnavailable(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class LLMProvider:
    def __init__(self) -> None:
        self._status_cache: Optional[Dict[str, Any]] = None
        self._status_at = 0.0
        self._override = None        # tests inject a callable(messages, json_mode) -> str

    @property
    def model(self) -> str:
        return settings.OLLAMA_MODEL

    def set_override(self, fn) -> None:
        self._override = fn
        self._status_cache = None

    def status(self, force: bool = False) -> Dict[str, Any]:
        if self._override is not None:
            return {"available": True, "model": "test-double", "error": None}
        if not force and self._status_cache and time.time() - self._status_at < 15:
            return self._status_cache
        info: Dict[str, Any] = {"available": False, "model": self.model, "error": None, "installed_models": []}
        try:
            r = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=2.0)
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
            info["installed_models"] = names
            if any(n == self.model or n.split(":")[0] == self.model.split(":")[0] for n in names):
                info["available"] = True
            else:
                info["error"] = (f"Ollama is running but model '{self.model}' is not installed. "
                                 f"Run: ollama pull {self.model}")
        except Exception:
            info["error"] = f"Ollama is not reachable at {settings.OLLAMA_BASE_URL}. Start it with: ollama serve"
        self._status_cache, self._status_at = info, time.time()
        return info

    def available(self) -> bool:
        return bool(self.status()["available"])

    def chat(self, messages: List[Dict[str, str]], json_mode: bool = False, temperature: float = 0.2,
             num_predict: int = 1500) -> str:
        if self._override is not None:
            return self._override(messages, json_mode)
        st = self.status()
        if not st["available"]:
            raise LLMUnavailable(st["error"] or "LLM unavailable")
        payload: Dict[str, Any] = {"model": self.model, "messages": messages, "stream": False,
                                   "options": {"temperature": temperature, "num_predict": num_predict}}
        if json_mode:
            payload["format"] = "json"
        try:
            r = httpx.post(f"{settings.OLLAMA_BASE_URL}/api/chat", json=payload, timeout=settings.OLLAMA_TIMEOUT_SEC)
            r.raise_for_status()
            return (r.json().get("message", {}) or {}).get("content", "").strip()
        except httpx.TimeoutException as exc:
            raise LLMError("The language model took too long to respond. Try a smaller model or shorter request.") from exc
        except httpx.HTTPError as exc:
            self._status_cache = None
            raise LLMError(f"The language model request failed ({type(exc).__name__}).") from exc

    def chat_json(self, messages: List[Dict[str, str]], temperature: float = 0.3, num_predict: int = 3500) -> Any:
        raw = self.chat(messages, json_mode=True, temperature=temperature, num_predict=num_predict)
        return parse_json_loose(raw)


def parse_json_loose(raw: str) -> Any:
    """Parse JSON from an LLM reply, tolerating code fences and leading/trailing prose."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("[", "]"), ("{", "}")):
        s, e = raw.find(open_c), raw.rfind(close_c)
        if s != -1 and e > s:
            try:
                return json.loads(raw[s:e + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("The language model returned malformed JSON.")


llm_provider = LLMProvider()
