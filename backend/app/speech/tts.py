"""Text-to-speech with Piper (open source).  Voices are configured per language via env vars and
are only reported as available when the voice file really exists - nothing is assumed."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional

from app.config import settings

LANGS = {"English": "EN", "Hindi": "HI", "Tamil": "TA", "Kannada": "KN", "Malayalam": "ML"}


class TTSUnavailable(RuntimeError):
    pass


def _voice_path(language: str) -> Optional[Path]:
    key = LANGS.get(language)
    if not key or not settings.PIPER_VOICES_DIR:
        return None
    name = getattr(settings, f"PIPER_VOICE_{key}", "")
    if not name:
        return None
    p = Path(settings.PIPER_VOICES_DIR) / name
    return p if p.exists() else None


def piper_installed() -> bool:
    return shutil.which(settings.PIPER_BIN) is not None


def availability() -> Dict[str, bool]:
    ok = piper_installed()
    return {lang: bool(ok and _voice_path(lang)) for lang in LANGS}


def synthesize(text: str, language: str) -> bytes:
    voice = _voice_path(language)
    if not piper_installed() or voice is None:
        raise TTSUnavailable(f"No server voice is configured for {language}.")
    text = text.strip()[:2000]
    if not text:
        raise TTSUnavailable("Nothing to speak.")
    try:
        proc = subprocess.run([settings.PIPER_BIN, "--model", str(voice), "--output_file", "-"],
                              input=text.encode("utf-8"), capture_output=True, timeout=120, check=True)
    except (subprocess.SubprocessError, OSError) as exc:
        raise TTSUnavailable("Text-to-speech failed to run.") from exc
    return proc.stdout
