"""Capture real responder/critic outputs for the offline shift replay."""

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.service import ROOT, ShiftService


def main():
    service = ShiftService()
    rows = []
    for exception in service.replay(600).exceptions:
        minute = int((exception.opened_at - service.initial.shift_start).total_seconds() / 60)
        sample = ShiftService(start_minute=minute)
        sample.assess_exception(exception.id)
        exchange = sample.exchanges[-1]
        rows.append({
            "event_id": exception.opening_event_id,
            "at": exchange.at.isoformat(),
            "reply": exchange.reply.model_dump(mode="json"),
            "chunks": [chunk.model_dump(mode="json") for chunk in exchange.chunks],
            "assessment": asdict(exchange.assessment),
        })
        print(f"{exception.opened_at:%H:%M} {exception.exception_type.value}: "
              f"{exchange.assessment.confidence:.3f} {exchange.reply.mode.value}", flush=True)
    target = ROOT / "data" / "seed" / "replay_assessments.json"
    target.write_text(json.dumps({
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "fixture_sha256": hashlib.sha256((ROOT / "eval/fixtures/fixture_events.json").read_bytes()).hexdigest(),
        "exchanges": rows,
    }, ensure_ascii=False, indent=2, default=lambda value: value.model_dump(mode="json")) + "\n")


if __name__ == "__main__":
    main()
