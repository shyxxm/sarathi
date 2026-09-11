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

import inspect  # noqa: E402

import litellm  # noqa: E402

from app.agents import interpreter  # noqa: E402
from app.agents.interpreter import TRANSIENT, cheap_model, interpret, system_prompt  # noqa: E402

# The only rows kept out of the score: the provider did not answer (TRANSIENT,
# after interpret's own retries), or could not be asked at all — keys, model
# name, spend, a parameter the model does not take. Those fail every row alike.
# Everything else is the model answering and getting it wrong: prose instead
# of JSON, a shape the contract refuses, a policy block on his words.
#
# Named, not caught-all. The catch-all is how a model answering in prose sat
# in the same bucket as a 429 and a whole failure class never reached the score.
NOT_ANSWERED = TRANSIENT + (
    litellm.exceptions.AuthenticationError,
    litellm.exceptions.PermissionDeniedError,
    litellm.exceptions.NotFoundError,
    litellm.exceptions.BudgetExceededError,
    litellm.exceptions.UnsupportedParamsError,
)

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

    So is the interpreter's own source. Adding the JSON prefill changed how
    every call is made without touching the prompt, and the first scored run
    after it replayed 23 cached answers from the old call shape and called
    nothing.
    """
    material = "\0".join([system_prompt(), inspect.getsource(interpreter), model,
                          case["id"], case["text"]])
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


def grade(case: dict, output) -> bool | None:
    """True, False, or None for a row that never reached the model.

    None is not False. A 429 is not the interpreter getting the answer wrong,
    and counting it as one understates the prompt by however many calls the
    provider refused. Errors are their own bucket and never enter the score.

    A destroyed row is graded on the two things that matter: it refused to
    invent an event, and it said so. Intents are not graded there — if the words
    cannot be read, whether he was reporting or asking cannot be either.

    A recoverable row is graded like any other message, because it is one. SPEC
    2: degradation that preserves meaning is not illegibility, and an
    interpreter that answers UNCLEAR to every damaged transcript is no use on
    the audio this system will actually get.
    """
    if output is None:
        return None
    event = output.event_type.value if output.event_type else None
    if case["degradation"] == "destroyed":
        return event == "UNCLEAR" and output.transcript_legible is False

    expected_legible = case.get("expected_transcript_legible")
    if expected_legible is not None and output.transcript_legible is not expected_legible:
        return False

    # Graded where the row states it, because this is what broke the question
    # path: intents and event_type were right every time and a stray
    # `event_type` in unresolved_fields escalated the message anyway. A score
    # that ignores this field cannot see that failure at all.
    expected_unresolved = case.get("expected_unresolved_fields")
    if (expected_unresolved is not None
            and sorted(output.unresolved_fields) != sorted(expected_unresolved)):
        return False
    return (sorted(intent.value for intent in output.intents)
            == sorted(case["expected_intents"])
            and event == case["expected_event_type"])


def run_one(case: dict, model: str) -> dict:
    try:
        output, error = interpret(case["text"], model=model), None
        ok = grade(case, output)
    except NOT_ANSWERED as exc:
        output, error, ok = None, f"{type(exc).__name__}: {exc}"[:120], None
    except Exception as exc:                                  # noqa: BLE001
        output, error, ok = None, f"{type(exc).__name__}: {exc}"[:120], False
    return {
        **case,
        "ok": ok,
        "error": error,
        "got_intents": [intent.value for intent in output.intents] if output else [],
        "got_event": (output.event_type.value if output.event_type else None) if output else None,
        "got_legible": output.transcript_legible if output else None,
        "got_hint": output.location_hint if output else None,
        "got_claimed": output.driver_claimed_wait_minutes if output else None,
        "got_unresolved": list(output.unresolved_fields) if output else [],
    }


# What a cached row replays. Everything else comes from the golden file.
RECORDED = ("ok", "error", "got_intents", "got_event", "got_legible",
            "got_hint", "got_claimed", "got_unresolved")


def tally(rows: list[dict]) -> str:
    """Scored, errored and total, kept apart. A run where the provider refused
    ten calls has no business printing 1/11 as if it were a score.

    Rows marked `expected_failure` are removed before this is called. They are
    documented boundaries (SPEC 2.1), not defects, and counting them as either
    correct or wrong misrepresents the run.
    """
    scored = [row for row in rows if row["ok"] is not None]
    errored = len(rows) - len(scored)
    correct = sum(1 for row in scored if row["ok"])
    if not scored:
        return f"nothing scored  ·  {errored} errored  ·  {len(rows)} total"
    line = f"{correct}/{len(scored)} correct"
    if errored:
        line += f"  ·  {errored} errored, not scored"
    return f"{line}  ·  {len(rows)} total"


def show(title: str, rows: list[dict]) -> None:
    if not rows:
        return
    print(f"\n{title}")
    for row in rows:
        got = f"{'+'.join(row['got_intents']) or '-'} / {row['got_event'] or '-'}"
        if row["degradation"] == "destroyed":
            got += f" / legible={row['got_legible']}"
            expected = "UNCLEAR / legible=False"
        else:
            expected = f"{'+'.join(row['expected_intents'])} / {row['expected_event_type']}"
            if row.get("expected_transcript_legible") is not None:
                got += f" / legible={row['got_legible']}"
                expected += f" / legible={row['expected_transcript_legible']}"
            if row.get("expected_unresolved_fields") is not None:
                got += f" / unresolved={'+'.join(row.get('got_unresolved') or []) or '-'}"
                expected += f" / unresolved={'+'.join(row['expected_unresolved_fields']) or '-'}"
        mark = {True: "  ", False: "X ", None: "! "}[row["ok"]]
        print(f"{mark}{row['id']:<6}{row['text'][:48]:<50}{got:<50}{expected}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", action="store_true",
                        help="run on LITELLM_MODEL_CHEAP instead of the local model")
    parser.add_argument("--pace", type=float, default=None,
                        help="seconds between calls; defaults to 0 local, 5 hosted")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore cached rows and call for every message")
    parser.add_argument("--only", default=None,
                        help="comma-separated message ids, for a fast iteration loop")
    arguments = parser.parse_args()

    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    model = cheap_model() if arguments.score else local_model()
    pace = arguments.pace if arguments.pace is not None else (5.0 if arguments.score else 0.0)
    print(f"model: {model}" + ("" if arguments.score else "   (local — not a quality signal)"))

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    if arguments.only:
        wanted = {one.strip() for one in arguments.only.split(",")}
        golden = [case for case in golden if case["id"] in wanted]
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
        if row["ok"] is not None:
            # Only answers are cached — a wrong one included. A 503 is not a
            # result; prose instead of JSON is.
            cache[case["id"]] = {"fingerprint": fingerprint(case, model),
                                 "row": {key: row[key] for key in RECORDED}}
            save_cache(model, cache)

    if reused:
        print(f"reused {reused} cached row(s), called {called}")

    boundaries = [row for row in rows if row.get("expected_failure")]
    rows = [row for row in rows if not row.get("expected_failure")]

    for band, label in (("none", "CLEAN"),
                        ("recoverable", "DEGRADED, MEANING INTACT — read it, do not refuse it"),
                        ("destroyed", "DEGRADED PAST RECOVERY — must refuse")):
        subset = [row for row in rows if row["degradation"] == band]
        show(f"{label}  ·  held out", [row for row in subset if not row["in_prompt"]])
        show(f"{label}  ·  in prompt — recited, not evidence",
             [row for row in subset if row["in_prompt"]])

    if boundaries:
        print("\n" + "-" * 78)
        print("DOCUMENTED BOUNDARIES — SPEC 2.1, not counted in the score")
        for row in boundaries:
            got = f"{'+'.join(row['got_intents']) or '-'} / {row['got_event'] or '-'}"
            if row.get("expected_transcript_legible") is not None:
                got += f" / legible={row['got_legible']}"
            print(f"\n  {row['id']}  {row['text']}")
            print(f"      got  {got}")
            print(f"      why  {row['note']}")
            if row["ok"]:
                print("      NOTE the boundary moved: this row now passes. That is a"
                      " finding. Do not quietly promote it — work out what changed.")
        print("-" * 78)

    print()
    for label, subset in (("held out", [r for r in rows if not r["in_prompt"]]),
                          ("in prompt", [r for r in rows if r["in_prompt"]])):
        print(f"{label:<10}{tally(subset)}")

    errored = [row for row in rows if row["ok"] is None]
    if errored:
        print(f"\n{len(errored)} row(s) never reached the model — not scored")
        for row in errored:
            print(f"  {row['id']:<6}{row['error']}")

    failures = [row for row in rows if row["ok"] is False]
    print(f"\n{len(failures)} mismatch(es)")
    for row in failures:
        band = "" if row["degradation"] == "none" else f"  ({row['degradation']})"
        print(f"\n  {row['id']}{band}  {row['text']}")
        print(f"      got      {'+'.join(row['got_intents']) or '-'} / {row['got_event'] or '-'}"
              f"   legible={row['got_legible']}   claimed={row['got_claimed']}")
        expected = ("UNCLEAR / legible=False" if row["degradation"] == "destroyed"
                    else f"{'+'.join(row['expected_intents'])} / {row['expected_event_type']}")
        print(f"      expected {expected}")
        if row.get("error"):
            print(f"      error    {row['error']}")
        if row.get("note"):
            print(f"      why      {row['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
