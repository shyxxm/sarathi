import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from threading import Event
from types import SimpleNamespace
from uuid import uuid4
import wave

from fastapi.testclient import TestClient
import pytest

from app import tracing
from app.api.main import create_app
from app.api.service import ShiftService
from app.contracts.enums import EventType, Intent, Language, ReplyMode
from app.contracts.event import InterpreterOutput
from app.contracts.reply import Claim, DriverReply
from app.voice import tts


def wav_bytes():
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x01\x00" * 240)
    return output.getvalue()


@pytest.fixture
def reply():
    return DriverReply(mode=ReplyMode.SPEAK, language=Language.ML,
                       text="Aluva-യിൽ 10:12 മുതൽ 60 മിനിറ്റ്.",
                       restated_facts=["FACTS MUST NOT BE ADDED"],
                       claims=[Claim(text="CLAIMS MUST NOT BE ADDED", cited_chunk_id="sop")],
                       cited_sop_ids=["sop"])


@pytest.mark.parametrize("provider,key", [("", "key"), ("sarvam", ""), ("unknown", "key")])
def test_missing_configuration_is_text_only(monkeypatch, reply, provider, key):
    monkeypatch.setenv("TTS_PROVIDER", provider)
    monkeypatch.setenv("SARVAM_API_KEY", key)
    monkeypatch.setattr(tts, "urlopen", lambda *a, **kw: pytest.fail("No provider call expected"))
    result = tts.ReplyAudio().render("reply", reply)
    assert result.audio is None and result.fallback_reason == "not_configured"


@pytest.mark.parametrize("language,code", [(Language.ML, "ml-IN"), (Language.MIXED, "ml-IN"),
                                         (Language.EN, "en-IN"), (Language.HI, "hi-IN")])
def test_sarvam_sends_exact_prose_and_decodes_wav(monkeypatch, reply, language, code):
    audio, calls = wav_bytes(), []
    def send(request, *, timeout):
        calls.append(json.loads(request.data))
        assert request.full_url == "https://api.sarvam.ai/text-to-speech"
        assert request.get_header("Api-subscription-key") == "test-key"
        assert timeout == 20
        return BytesIO(json.dumps({"audios": [base64.b64encode(audio).decode()]}).encode())
    monkeypatch.setattr(tts, "urlopen", send)
    cache = tts.ReplyAudio(lambda: tts.SarvamTTS("test-key"))
    result = cache.render("reply", reply.model_copy(update={"language": language}))
    assert result.audio == audio and result.fallback_reason is None
    assert calls[0]["text"] == reply.text and calls[0]["language_code"] == code
    assert calls[0]["model"] == "bulbul:v3"
    assert cache.render("reply", reply) is result and len(calls) == 1


@pytest.mark.parametrize("payload", [{}, {"audios": []}, {"audios": ["not base64!"]},
                                     {"audios": [base64.b64encode(b"not wav").decode()]}])
def test_bad_provider_audio_falls_back_and_is_cached(monkeypatch, reply, payload):
    calls = []
    def send(*args, **kwargs):
        calls.append(1)
        return BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(tts, "urlopen", send)
    cache = tts.ReplyAudio(lambda: tts.SarvamTTS("test-key"))
    result = cache.render("reply", reply)
    assert result.audio is None and result.fallback_reason
    assert cache.render("reply", reply) is result and len(calls) == 1


def test_timeout_is_traced_under_finished_message_and_cached(reply, langfuse):
    def fail(*args):
        raise TimeoutError("secret provider body")
    cache = tts.ReplyAudio(lambda: SimpleNamespace(synthesise=fail))
    with tracing.trace("driver message", seed="m06", trip_id="trip-1", message_id="m06") as root:
        parent = root.parent()
    assert not langfuse._open
    result = cache.render("reply", reply, parent=parent)
    node = langfuse.named("tts")
    assert node.trace_context == {"trace_id": "trace-m06", "parent_span_id": "span-0"}
    assert node.fields["input"] == reply.text
    assert node.fields["metadata"]["fell_back_to_text"] is True
    assert node.fields["metadata"]["latency_ms"] >= 0
    assert result.fallback_reason == "TimeoutError"
    assert "secret" not in str(node.fields)
    cache.render("reply", reply, parent=parent)
    assert len(langfuse.observations) == 2


def test_slow_audio_never_blocks_text_or_shift_and_concurrent_replays_share_it(monkeypatch, langfuse):
    shift = ShiftService()
    monkeypatch.setattr(shift, "interpret", lambda text: InterpreterOutput(
        intents=[Intent.REPORT], language=Language.ML, event_type=EventType.SERVICE_STARTED))
    app = create_app(shift)
    entered, release = Event(), Event()
    spoken = []
    def slow(text, language):
        spoken.append(text)
        entered.set()
        assert release.wait(5)
        return wav_bytes()
    app.state.reply_audio = tts.ReplyAudio(lambda: SimpleNamespace(synthesise=slow))
    with TestClient(app) as client, ThreadPoolExecutor(2) as pool:
        message_id = str(uuid4())
        response = client.post("/driver/messages", data={"text": "Unloading now", "message_id": message_id})
        assert response.status_code == 200 and not entered.is_set()
        exchange = shift.exchanges[-1]
        url = f"/driver/replies/{exchange.id}/audio"
        assert f'data-reply-id="{exchange.id}"' in response.text
        first = pool.submit(client.get, url)
        try:
            assert entered.wait(2)
            second = pool.submit(client.get, url)
            assert client.get("/driver/updates").status_code == 200
            assert client.get("/dispatcher").status_code == 200
            shift.advance()
            assert not first.done()
        finally:
            release.set()
        assert first.result().content == second.result().content == wav_bytes()
        assert client.get(url).content == wav_bytes()
        assert spoken == [exchange.reply.text]
        assert langfuse.named("tts").trace_context == {
            "trace_id": f"trace-{message_id}", "parent_span_id": "span-0"}
        assert langfuse.named("tts").fields["metadata"]["fell_back_to_text"] is False
        assert client.get("/driver/replies/unknown/audio").status_code == 404


def test_audio_failure_keeps_reply_and_trip(monkeypatch):
    shift, calls = ShiftService(), []
    def broken():
        calls.append(1)
        raise RuntimeError("secret key must not escape")
    app = create_app(shift)
    app.state.reply_audio = tts.ReplyAudio(broken)
    with TestClient(app) as client:
        before, state = client.get("/driver").text, shift.state
        exchange = shift.recorded_exchanges(state)[-1]
        for _ in range(2):
            response = client.get(f"/driver/replies/{exchange.id}/audio")
            assert response.status_code == 204 and not response.content
        assert calls == [1] and shift.state is state
        assert exchange.reply.text in before and exchange.reply.text in client.get("/driver").text


@pytest.mark.parametrize("broken", ["exploding", "brittle"])
def test_broken_tracing_cannot_break_audio(monkeypatch, broken_langfuse, broken, reply):
    backend = broken_langfuse[broken]
    monkeypatch.setattr(tracing, "_load", lambda: (backend, backend.propagate))
    parent = {"trace_context": {"trace_id": "t", "parent_span_id": "s"}, "ids": {"trip_id": "trip-1"}}
    cache = tts.ReplyAudio(lambda: SimpleNamespace(synthesise=lambda *args: wav_bytes()))
    assert cache.render("reply", reply, parent=parent).audio == wav_bytes()
