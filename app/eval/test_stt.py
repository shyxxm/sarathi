from io import BytesIO
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app import tracing
from app.agents import responder, safety_critic
from app.api.main import create_app
from app.api.service import ShiftService
from app.contracts.enums import EventType, Intent, Language
from app.contracts.event import InterpreterOutput
from app.contracts.reply import ResponderOutput
from app.contracts.retrieval import RetrievalResult
from app.voice import stt
from scripts.check_stt import grade, read_notes, word_error_rate, words


def speech(result):
    return stt.SpeechInput(lambda: SimpleNamespace(name="test", transcribe=lambda *args: result))


@pytest.mark.parametrize("segments,overall,expected", [((.99, .12, .9), .9, .12),
                                                       ((.99, 0, .9), .9, 0),
                                                       ((.9, None), .9, None),
                                                       ((), .7, .7), ((), None, None)])
def test_confidence_is_the_weakest_segment_not_the_average(segments, overall, expected):
    assert stt.Transcription("ഗേറ്റ് തുറന്നു", overall, segments).stt_confidence == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -.1, 1.1, "0.9", True])
def test_invalid_confidence_is_not_normalised_into_a_measurement(value):
    with pytest.raises(ValueError):
        stt.Transcription("text", confidence=value)


def test_sarvam_language_probability_is_never_transcription_confidence(monkeypatch):
    payload = {"transcript": "gate തുറന്നു", "language_code": "ml-IN", "language_probability": .99,
               "timestamps": {"words": ["gate തുറന്നു"], "start_time_seconds": [0], "end_time_seconds": [1]}}
    def send(request, *, timeout):
        assert request.full_url == "https://api.sarvam.ai/speech-to-text"
        assert request.get_header("Api-subscription-key") == "test-key"
        assert timeout == 30 and b'saaras:v3' in request.data and b'codemix' in request.data
        assert b'with_timestamps' in request.data and b'audio bytes unchanged' in request.data
        assert b'translate' not in request.data
        return BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(stt, "urlopen", send)
    result = stt.SarvamSTT("test-key").transcribe(b"audio bytes unchanged", "audio/webm")
    assert result.text == payload["transcript"]
    assert result.stt_confidence is None and result.language_probability == .99


@pytest.mark.parametrize("provider,key", [("", "key"), ("sarvam", ""), ("other", "key")])
def test_unconfigured_stt_returns_no_transcript(monkeypatch, provider, key):
    monkeypatch.setenv("STT_PROVIDER", provider)
    monkeypatch.setenv("SARVAM_API_KEY", key)
    monkeypatch.setattr(stt, "urlopen", lambda *args, **kwargs: pytest.fail("Must not call a provider"))
    with pytest.raises(stt.STTUnavailable, match="not configured"):
        stt.SpeechInput().transcribe(b"audio", "audio/wav")


@pytest.mark.parametrize("payload", [{}, {"transcript": ""}, {"transcript": "   "},
                                     {"transcript": None}, {"transcript": 42}, {"transcript": "x" * 2001}])
def test_bad_provider_output_never_becomes_words(monkeypatch, payload):
    monkeypatch.setattr(stt, "urlopen", lambda *a, **k: BytesIO(json.dumps(payload).encode()))
    with pytest.raises(stt.STTUnavailable, match="transcription failed"):
        stt.SpeechInput(lambda: stt.SarvamSTT("key")).transcribe(b"audio", "audio/wav")


def setup_shift(monkeypatch):
    shift = ShiftService(start_minute=132)
    shift.advance()
    heard = []
    def interpret(text):
        heard.append(text)
        return InterpreterOutput(intents=[Intent.REPORT], event_type=EventType.GATE_CLOSED, language=Language.ML)
    monkeypatch.setattr(shift, "interpret", interpret)
    monkeypatch.setattr(shift, "retrieve", lambda *args: RetrievalResult())
    monkeypatch.setattr(responder, "respond", lambda *args: ResponderOutput(language=Language.ML, text="Gate closed.", confidence=.9))
    monkeypatch.setattr(safety_critic, "ground", lambda *args, **kwargs: safety_critic.GroundingOutput(verdicts=[]))
    return shift, heard


@pytest.mark.parametrize("confidence,segments,expected", [(None, (), 0), (.85, (), .85), (.99, (.9, .1), .1)])
def test_voice_uses_typed_pipeline_with_real_confidence_and_trace(monkeypatch, langfuse, confidence, segments, expected):
    shift, heard = setup_shift(monkeypatch)
    result = stt.Transcription("  gate അടച്ചിരിക്കുന്നു.\n", confidence, segments, language_probability=.99)
    app = create_app(shift)
    calls = []
    def provider_call(audio, mime):
        calls.append((audio, mime))
        return result
    app.state.speech_input = stt.SpeechInput(lambda: SimpleNamespace(name="test", transcribe=provider_call))
    with TestClient(app) as client:
        message_id = str(uuid4())
        url = f"/driver/voice-messages/{message_id}"
        for _ in range(2):
            response = client.post(url, content=b"original audio", headers={"Content-Type": "audio/webm;codecs=opus"})
            assert response.status_code == 200
        exchange = shift.exchanges[-1]
        assert calls == [(b"original audio", "audio/webm")]
        assert heard == [result.text] and exchange.transcript == result.text
        assert shift.state.events[-1].raw_transcript == result.text
        assert exchange.assessment.signals["transcript_legible"] == expected
        assert exchange.transcription is result
        assert "Transcribed from your voice note" in client.get("/driver").text
        if confidence is None:
            assert "unavailable — zero credit" in client.get("/dispatcher").text
        span = langfuse.named("stt")
        assert span.parent == "driver message"
        assert span.fields["output"] == result.text
        assert span.fields["metadata"]["stt_confidence"] == result.stt_confidence
        assert span.fields["metadata"]["provider"] == "test"
        assert span.fields["metadata"]["latency_ms"] >= 0
        assert span.fields["metadata"]["failed"] is False
        # A later dispatcher safety check must not regain the missing .25.
        opened = next(ex for ex in shift.state.exceptions if ex.opening_event_id == exchange.id)
        shift.assess_exception(opened.id)
        assert shift.exchanges[-1].assessment.signals["transcript_legible"] == expected


def test_voice_without_confidence_loses_exactly_a_quarter_against_typed(monkeypatch):
    typed, _ = setup_shift(monkeypatch)
    voice, _ = setup_shift(monkeypatch)
    typed.submit("gate closed", str(uuid4()))
    voice.submit_voice(b"audio", "audio/wav", str(uuid4()), speech(stt.Transcription("gate closed", language_probability=1)))
    assert typed.exchanges[-1].assessment.confidence - voice.exchanges[-1].assessment.confidence == pytest.approx(.25)
    assert voice.exchanges[-1].transcription.stt_confidence is None


def test_stt_failure_does_not_create_a_message_or_invent_a_transcript(monkeypatch, langfuse):
    shift, heard = setup_shift(monkeypatch)
    def fail(*args):
        raise TimeoutError("secret provider error body")
    app = create_app(shift)
    app.state.speech_input = stt.SpeechInput(lambda: SimpleNamespace(name="sarvam", transcribe=fail))
    before = shift.state
    with TestClient(app) as client:
        response = client.post(f"/driver/voice-messages/{uuid4()}", content=b"audio", headers={"Content-Type": "audio/wav"})
        assert response.status_code == 503
        assert "transcription failed" in response.json()["error"]
        assert "secret" not in response.text
        assert shift.state is before and not shift.exchanges and not heard
        assert not shift.pending_messages and not shift.completed_messages
        assert 'id="driver-text"' in client.get("/driver").text
        span = langfuse.named("stt")
        assert span.fields["output"] is None and span.fields["metadata"]["failed"]
        assert "secret" not in str(span.fields)
        # Typed input still works after a provider outage.
        response = client.post("/driver/messages", data={"message_id": str(uuid4()), "text": "Gate closed."})
        assert response.status_code == 200 and heard == ["Gate closed."]


def test_upload_bounds_are_checked_before_provider_or_interpreter(monkeypatch):
    shift, heard = setup_shift(monkeypatch)
    app = create_app(shift)
    app.state.speech_input = SimpleNamespace(transcribe=lambda *args: pytest.fail("Must not transcribe"))
    with TestClient(app) as client:
        url = f"/driver/voice-messages/{uuid4()}"
        assert client.post(url, content=b"", headers={"Content-Type": "audio/wav"}).status_code == 422
        assert client.post(url, content=b"audio", headers={"Content-Type": "text/plain"}).status_code == 415
        assert client.post(url, content=b"x" * (stt.MAX_AUDIO_BYTES + 1), headers={"Content-Type": "audio/wav"}).status_code == 413
        assert client.post("/driver/voice-messages/not-a-uuid", content=b"audio", headers={"Content-Type": "audio/wav"}).status_code == 422
        assert not heard and not shift.exchanges


def test_wer_keeps_malayalam_marks_and_negation():
    assert words("ഗേറ്റ് തുറന്നു.") == ["ഗേറ്റ്", "തുറന്നു"]
    assert word_error_rate("ഇവിടെ ആരും ഇല്ല", "ഇവിടെ ആരും") == {"word_edits": 1, "reference_words": 3, "wer": 1/3}
    assert word_error_rate("Gate opened", "gate opened.")["wer"] == 0
    assert word_error_rate("Reached.", "എത്തി.", reference_kind="translation") == {}


def test_evaluation_needs_real_audio_and_human_reference(tmp_path):
    with pytest.raises(ValueError, match="No voice notes"):
        read_notes(tmp_path)
    (tmp_path / "m07-quiet.wav").write_bytes(b"audio")
    with pytest.raises(ValueError, match="Missing human transcript"):
        read_notes(tmp_path)
    assert read_notes(tmp_path, require_references=False)[0][1:] == (None, None)
    (tmp_path / "m07-quiet.txt").write_text("gate thurannu irakkan thudangi")
    assert read_notes(tmp_path)[0][2] is None  # A filename is not a ground truth label.
    (tmp_path / "m07-quiet.json").write_text(json.dumps({"event_type": "SERVICE_STARTED", "intents": ["REPORT"]}))
    _, _, expected = read_notes(tmp_path)[0]
    assert grade({"event_type": "GATE_CLOSED", "intents": ["REPORT"]}, expected) is False
    assert grade({"event_type": "SERVICE_STARTED", "intents": ["REPORT"]}, expected) is True
