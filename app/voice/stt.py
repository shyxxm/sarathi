"""Speech becomes text once, before the existing interpreter pipeline."""

from dataclasses import dataclass
import json
import logging
import math
import os
from time import perf_counter
from typing import Protocol
from urllib.request import Request, urlopen
from uuid import uuid4

from dotenv import load_dotenv

from app import tracing

LOG = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MEDIA_TYPES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav",
               "audio/x-wav": "wav", "audio/mp4": "m4a", "audio/mpeg": "mp3",
               "audio/aac": "aac", "audio/flac": "flac"}


class STTUnavailable(Exception):
    """No transcript to submit. Never interpret this as the driver's words."""


@dataclass(frozen=True)
class Transcription:
    text: str
    confidence: float | None = None
    segment_confidences: tuple[float | None, ...] = ()
    language_code: str | None = None
    language_probability: float | None = None

    def __post_init__(self):
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("No speech was transcribed")
        for value in (self.confidence, *self.segment_confidences, self.language_probability):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or not 0 <= value <= 1):
                raise ValueError("Confidence must be a finite probability or unavailable")

    @property
    def stt_confidence(self) -> float | None:
        if self.segment_confidences:
            # Missing a segment's score is missing evidence. Never hide it in
            # an average of the other segments, or in the overall confidence.
            if any(value is None for value in self.segment_confidences):
                return None
            return min(self.segment_confidences)
        return self.confidence


class STTProvider(Protocol):
    name: str

    def transcribe(self, audio: bytes, media_type: str) -> Transcription:
        """Return the provider's text and measured confidence, or raise."""
        ...


class SarvamSTT:
    name = "sarvam"
    model = "saaras:v3"

    def __init__(self, key: str, *, timeout: float = 30):
        self.key, self.timeout = key, timeout

    def response(self, audio: bytes, media_type: str) -> dict:
        """The raw response is also kept by the evaluation script."""
        validate_audio(audio, media_type)
        boundary = uuid4().hex
        fields = {"model": self.model, "mode": "codemix", "language_code": "unknown",
                  "with_timestamps": "true"}
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                 for key, value in fields.items()]
        parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                  f'filename="note.{MEDIA_TYPES[media_type]}"\r\nContent-Type: {media_type}\r\n\r\n'.encode(),
                  audio, f"\r\n--{boundary}--\r\n".encode()]
        request = Request("https://api.sarvam.ai/speech-to-text", method="POST",
                          headers={"api-subscription-key": self.key,
                                   "Content-Type": f"multipart/form-data; boundary={boundary}"},
                          data=b"".join(parts))
        with urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    @staticmethod
    def parse(payload: dict) -> Transcription:
        # Saaras v3 currently supplies phrase timestamps and language detection
        # probability, NOT transcription confidence. Do not rename that field
        # or invent scores from timestamps. Other providers can supply genuine
        # overall/segment confidence through the same interface.
        return Transcription(text=payload["transcript"],
                             language_code=payload.get("language_code"),
                             language_probability=payload.get("language_probability"))

    def transcribe(self, audio: bytes, media_type: str) -> Transcription:
        return self.parse(self.response(audio, media_type))


def validate_audio(audio: bytes, media_type: str):
    if media_type not in MEDIA_TYPES:
        raise ValueError("Unsupported audio format")
    if not 0 < len(audio) <= MAX_AUDIO_BYTES:
        raise ValueError("Audio must be between 1 byte and 8 MB")


def from_env() -> STTProvider | None:
    load_dotenv()
    provider = os.getenv("STT_PROVIDER", "").strip().lower()
    key = os.getenv("SARVAM_API_KEY", "").strip()
    return SarvamSTT(key) if provider == "sarvam" and key else None


class SpeechInput:
    def __init__(self, provider_factory=from_env):
        self._provider_factory = provider_factory

    def transcribe(self, audio: bytes, media_type: str) -> Transcription:
        with tracing.observe("stt", input={"audio_bytes": len(audio), "media_type": media_type}) as span:
            started, provider, result, failure = perf_counter(), None, None, None
            try:
                validate_audio(audio, media_type)
                provider = self._provider_factory()
                if provider is None:
                    raise STTUnavailable("Voice input is not configured. Please type your message.")
                result = provider.transcribe(audio, media_type)
                if not isinstance(result, Transcription) or len(result.text) > 2000:
                    raise ValueError("Transcript cannot be submitted")
                return result
            except STTUnavailable:
                failure = "not_configured"
                raise
            except Exception as error:
                failure = type(error).__name__
                LOG.warning("STT failed (%s); no transcript submitted", failure)
                raise STTUnavailable("Voice transcription failed. Nothing was recorded on your trip. Please type your message or record again.") from None
            finally:
                span.record(lambda: {"output": result.text if result else None,
                                     "metadata": {"provider": provider.name if provider else None,
                                                  "latency_ms": (perf_counter() - started) * 1000,
                                                  "stt_confidence": result.stt_confidence if result else None,
                                                  "segment_confidences": list(result.segment_confidences) if result else [],
                                                  "language_probability": result.language_probability if result else None,
                                                  "failed": failure is not None, "failure": failure}})
