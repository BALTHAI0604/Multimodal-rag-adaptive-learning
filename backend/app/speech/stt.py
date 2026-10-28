"""Speech-to-text with faster-whisper (open source, runs locally, no paid API).

The model is loaded lazily the first time it is needed.  If faster-whisper (or the model files)
are not available a `SpeechUnavailable` error is raised and callers fall back to typed answers.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import List, Optional

from app.config import settings

log = logging.getLogger("stt")

LANG_CODES = {"english": "en", "hindi": "hi", "tamil": "ta", "kannada": "kn", "malayalam": "ml",
              "en": "en", "hi": "hi", "ta": "ta", "kn": "kn", "ml": "ml"}


class SpeechUnavailable(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    language: Optional[str]
    duration: float
    segments: List[dict]
    engine: str


_model = None
_lock = threading.Lock()
_override = None            # tests inject a fake transcriber here


def set_transcriber_for_tests(fn) -> None:
    global _override
    _override = fn


def lang_code(language: Optional[str]) -> Optional[str]:
    if not language:
        return None
    return LANG_CODES.get(language.strip().lower())


def _load():
    global _model
    with _lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise SpeechUnavailable(
                    "Speech recognition is not installed on the server (pip install faster-whisper).") from exc
            try:
                _model = WhisperModel(settings.WHISPER_MODEL, device=settings.WHISPER_DEVICE,
                                      compute_type=settings.WHISPER_COMPUTE_TYPE)
            except Exception as exc:
                raise SpeechUnavailable(
                    f"Could not load the Whisper model '{settings.WHISPER_MODEL}': {type(exc).__name__}. "
                    "It is downloaded on first use, so the server needs internet once.") from exc
    return _model


def is_available() -> bool:
    if _override is not None:
        return True
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def transcribe_file(path: str, language: Optional[str] = None) -> Transcript:
    if _override is not None:
        return _override(path, language)
    model = _load()
    code = lang_code(language)
    try:
        segs, info = model.transcribe(path, language=code, vad_filter=True, beam_size=5)
        segments = [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()} for s in segs]
    except Exception as exc:
        raise SpeechUnavailable(f"Transcription failed ({type(exc).__name__}). The audio may be corrupt.") from exc
    text = " ".join(s["text"] for s in segments).strip()
    return Transcript(text=text, language=getattr(info, "language", code), duration=float(getattr(info, "duration", 0.0)),
                      segments=segments, engine=f"faster-whisper:{settings.WHISPER_MODEL}")
