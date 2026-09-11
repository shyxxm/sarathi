"""Run m06 live, then render its spoken text and measure a cached replay."""

import argparse
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import tracing
from app.api.service import ROOT, ShiftService
from app.voice.tts import ReplyAudio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/sarathi-m06"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    message = next(row for row in json.loads((ROOT / "eval/fixtures/messages.json").read_text())
                   if row["id"] == "m06")
    # The stop's recorded arrival is 10:12; m06 arrives one minute later.
    shift = ShiftService(start_minute=132)
    shift.advance()
    tracing.start()
    message_id = str(uuid4())
    started = perf_counter()
    shift.submit(message["text"], message_id)
    text_ms = (perf_counter() - started) * 1000
    exchange = shift.exchanges[-1]
    print(f"Text delivered in {text_ms:.1f} ms: {exchange.reply.mode.value}", flush=True)
    print(exchange.reply.text, flush=True)
    cache = ReplyAudio()
    parent = shift.reply_trace_parents.get(exchange.id)
    rendered = cache.render(exchange.id, exchange.reply, parent=parent)
    started = perf_counter()
    replay = cache.render(exchange.id, exchange.reply, parent=parent)
    replay_ms = (perf_counter() - started) * 1000
    assert replay is rendered
    duration = None
    if rendered.audio:
        (args.output_dir / "m06.wav").write_bytes(rendered.audio)
        with wave.open(BytesIO(rendered.audio)) as wav:
            duration = wav.getnframes() / wav.getframerate()
    else:
        # A failed rerun must not leave an older successful sample to review.
        (args.output_dir / "m06.wav").unlink(missing_ok=True)
    (args.output_dir / "m06.txt").write_text(exchange.reply.text + "\n")
    report = {"at": datetime.now(timezone.utc).isoformat(), "message_id": message_id,
              "reply_id": exchange.id, "mode": exchange.reply.mode.value,
              "text_latency_ms": text_ms, "tts_latency_ms": rendered.latency_ms,
              "cached_replay_ms": replay_ms, "fell_back_to_text": rendered.audio is None,
              "fallback_reason": rendered.fallback_reason, "audio_duration_seconds": duration,
              "has_malayalam": any("\u0d00" <= c <= "\u0d7f" for c in exchange.reply.text),
              "has_latin": any(c.isascii() and c.isalpha() for c in exchange.reply.text),
              "has_figures": any(c.isdigit() for c in exchange.reply.text),
              "pronunciation_review": (
                  "Listen to m06.wav against m06.txt; synthesis success is not a pronunciation verdict."
                  if rendered.audio else "No audio was generated; nothing to review.")}
    (args.output_dir / "m06.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    tracing.flush()


if __name__ == "__main__":
    main()
