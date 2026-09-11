"""Optional rendering of a routed reply. Never called by the message pipeline."""

import base64
from concurrent.futures import Future
from dataclasses import dataclass
from io import BytesIO
import json
import logging
import os
from threading import Lock
from time import perf_counter
from typing import Protocol
from urllib.request import Request, urlopen
import wave

from dotenv import load_dotenv

from app import tracing
from app.contracts.enums import Language
from app.contracts.reply import DriverReply

LOG = logging.getLogger(__name__)


class TTSProvider(Protocol):
    def synthesise(self, text: str, language: Language) -> bytes:
        """Return WAV audio, or raise. The caller owns text fallback."""
        ...


class SarvamTTS:
    def __init__(self, key: str, *, timeout: float = 20):
        self.key, self.timeout = key, timeout

    def synthesise(self, text: str, language: Language) -> bytes:
        if not 0 < len(text) <= 2500:
            raise ValueError("Sarvam needs between 1 and 2500 characters")
        # Preserve the routed prose, including English names and figures.
        # Bulbul v3 normalises mixed text itself; no translation or rewriting.
        language_code = {Language.ML: "ml-IN", Language.MIXED: "ml-IN",
                         Language.EN: "en-IN", Language.HI: "hi-IN"}[language]
        request = Request("https://api.sarvam.ai/text-to-speech", method="POST",
                          headers={"api-subscription-key": self.key,
                                   "Content-Type": "application/json"},
                          data=json.dumps({"text": text, "language_code": language_code,
                                           "model": "bulbul:v3", "speaker": "shubh",
                                           "output_audio_codec": "wav",
                                           "speech_sample_rate": 24000}).encode())
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        audios = payload["audios"]
        if not isinstance(audios, list) or len(audios) != 1:
            raise ValueError("Expected one audio for one reply")
        audio = base64.b64decode(audios[0], validate=True)
        with wave.open(BytesIO(audio)) as wav:
            if not wav.getnframes() or not wav.readframes(1):
                raise ValueError("Empty audio")
        return audio


def from_env() -> TTSProvider | None:
    load_dotenv()
    provider = os.getenv("TTS_PROVIDER", "").strip().lower()
    key = os.getenv("SARVAM_API_KEY", "").strip()
    return SarvamTTS(key) if provider == "sarvam" and key else None


@dataclass(frozen=True)
class RenderedAudio:
    audio: bytes | None
    latency_ms: float
    fallback_reason: str | None = None


class ReplyAudio:
    """One process-local cache, like the demo shift. Concurrent requests share
    one synthesis; failures are cached too, so page polls never retry a bill."""

    def __init__(self, provider_factory=from_env):
        self._provider_factory = provider_factory
        self._lock = Lock()
        self._replies: dict[str, Future] = {}

    def render(self, reply_id: str, reply: DriverReply, *, parent=None) -> RenderedAudio:
        with self._lock:
            future = self._replies.get(reply_id)
            first = future is None
            if first:
                future = self._replies[reply_id] = Future()
        if first:
            with tracing.observe("tts", parent=parent, input=reply.text) as span:
                started, audio, reason = perf_counter(), None, None
                try:
                    provider = self._provider_factory()
                    if provider is None:
                        reason = "not_configured"
                    else:
                        audio = provider.synthesise(reply.text, reply.language)
                        if not audio:
                            raise ValueError("Empty audio")
                except Exception as error:
                    # Exception bodies can contain provider credentials. Keep
                    # only the type in logs and traces, never the response body.
                    reason = type(error).__name__
                    audio = None
                    LOG.warning("TTS unavailable (%s); reply stays text", reason)
                result = RenderedAudio(audio, (perf_counter() - started) * 1000, reason)
                span.record(lambda: {"output": {"audio_available": audio is not None},
                                     "metadata": {"reply_id": reply_id,
                                                  "latency_ms": result.latency_ms,
                                                  "fell_back_to_text": audio is None,
                                                  "fallback_reason": reason}})
            future.set_result(result)
        return future.result()
