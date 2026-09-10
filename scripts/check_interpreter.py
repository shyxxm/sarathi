"""Run the message set through the interpreter and print what it got wrong.

    python scripts/check_interpreter.py           # local model, unmetered
    python scripts/check_interpreter.py --score   # hosted cheap tier

Iterate against the local model as much as you like; it is free and it is not
evidence. Only a --score run says anything about prompt quality, and only the
held-out rows in it count — a model reciting its own few-shot examples has told
you nothing.
"""

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from app.agents.interpreter import cheap_model, interpret, system_prompt  # noqa: E402

GOLDEN_PATH = ROOT / "app/eval/fixtures/golden_shift.json"
LOCAL_DEFAULT = "ollama/qwen2.5:7b"
CACHE_DIR = ROOT / ".interpreter_cache"


def local_model() -> str:
    return os.getenv("LITELLM_MODEL_LOCAL", LOCAL_DEFAULT)


def fingerprint(case: dict, model: str) -> str:
    """What a cached answer was an answer to.

    The prompt is in here, so editing prompts/interpreter.md invalidates every
    row — which is exactly when you want fresh calls, and exactly when a stale
    score would be worst.
    """
    material = "\0".join([system_prompt(), model, case["id"], case["text"]])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def cache_path(model: str) -> Path:
    return CACHE_DIR / f"{re.sub(r'[^A-Za-z0-9._-]', '_', model)}.json"


def load_cache(model: str) -> dict:
    path = cache_path(model)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}          # a half-written cache is not worth a crash


def save_cache(model: str, cache: dict) -> None:
    """Written after every row. The whole point is surviving a crash on row 14
    with thirteen good calls already spent against a 20-a-day budget."""
    path = cache_path(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")


def grade(case: dict, output) -> bool:
    """A garbled row is graded on the two things that matter: it refused to
    invent an event, and it said so. Intents are not graded there — if the
    words cannot be read, whether he was reporting or asking cannot be either."""
    if output is None:
        return False
    event = output.event_type.value if output.event_type else None
    if case["garbled"]:
        return event == "UNCLEAR" and output.transcript_legible is False
    return (sorted(intent.value for intent in output.intents)
            == sorted(case["expected_intents"])
            and event == case["expected_event_type"])


def run_one(case: dict, model: str) -> dict:
    try:
        output, error = interpret(case["text"], model=model), None
    except Exception as exc:                                  # noqa: BLE001
        output, error = None, f"{type(exc).__name__}: {exc}"[:120]
    return {
        **case,
        "ok": grade(case, output),
        "error": error,
        "got_intents": [intent.value for intent in output.intents] if output else [],
        "got_event": (output.event_type.value if output.event_type else None) if output else None,
        "got_legible": output.transcript_legible if output else None,
        "got_hint": output.location_hint if output else None,
        "got_claimed": output.driver_claimed_wait_minutes if output else None,
    }


# What a cached row replays. Everything else comes from the golden file.
RECORDED = ("ok", "error", "got_intents", "got_event", "got_legible",
            "got_hint", "got_claimed")


def show(title: str, rows: list[dict]) -> None:
    if not rows:
        return
    print(f"\n{title}")
    for row in rows:
        got = f"{'+'.join(row['got_intents']) or '-'} / {row['got_event'] or '-'}"
        if row["garbled"]:
            got += f" / legible={row['got_legible']}"
            expected = "UNCLEAR / legible=False"
        else:
            expected = f"{'+'.join(row['expected_intents'])} / {row['expected_event_type']}"
        mark = "  " if row["ok"] else "X "
        print(f"{mark}{row['id']:<6}{row['text'][:52]:<54}{got:<34}{expected}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", action="store_true",
                        help="run on LITELLM_MODEL_CHEAP instead of the local model")
    parser.add_argument("--pace", type=float, default=None,
                        help="seconds between calls; defaults to 0 local, 5 hosted")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore cached rows and call for every message")
    arguments = parser.parse_args()

    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    model = cheap_model() if arguments.score else local_model()
    pace = arguments.pace if arguments.pace is not None else (5.0 if arguments.score else 0.0)
    print(f"model: {model}" + ("" if arguments.score else "   (local — not a quality signal)"))

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cache = {} if arguments.fresh else load_cache(model)

    rows, called, reused = [], 0, 0
    for case in golden:
        cached = cache.get(case["id"])
        if cached and cached.get("fingerprint") == fingerprint(case, model):
            rows.append({**case, **cached["row"]})
            reused += 1
            continue

        if called and pace:
            time.sleep(pace)
        row = run_one(case, model)
        called += 1
        print(f"  {case['id']}", end="\r", file=sys.stderr)
        rows.append(row)
        if row["error"] is None:
            # Only answers are cached. A 503 is not a result.
            cache[case["id"]] = {"fingerprint": fingerprint(case, model),
                                 "row": {key: row[key] for key in RECORDED}}
            save_cache(model, cache)

    if reused:
        print(f"reused {reused} cached row(s), called {called}")

    for label, garbled in (("LEGIBLE", False), ("GARBLED", True)):
        subset = [row for row in rows if row["garbled"] is garbled]
        show(f"{label}  ·  held out", [row for row in subset if not row["in_prompt"]])
        show(f"{label}  ·  in prompt — recited, not evidence",
             [row for row in subset if row["in_prompt"]])

    held = [row for row in rows if not row["in_prompt"]]
    print(f"\nheld out {sum(row['ok'] for row in held)}/{len(held)}")

    failures = [row for row in rows if not row["ok"]]
    print(f"\n{len(failures)} mismatch(es)")
    for row in failures:
        print(f"\n  {row['id']}{'  (garbled)' if row['garbled'] else ''}  {row['text']}")
        print(f"      got      {'+'.join(row['got_intents']) or '-'} / {row['got_event'] or '-'}"
              f"   legible={row['got_legible']}   claimed={row['got_claimed']}")
        expected = ("UNCLEAR / legible=False" if row["garbled"]
                    else f"{'+'.join(row['expected_intents'])} / {row['expected_event_type']}")
        print(f"      expected {expected}")
        if row["error"]:
            print(f"      error    {row['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
