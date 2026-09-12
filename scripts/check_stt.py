"""Compare real voice notes with human transcripts and interpreter outcomes.

Put audio and a same-name .txt reference in recordings/stt. No denoising,
translation, prompt hints, or reference text is sent to the recogniser.
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
import unicodedata
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import tracing
from app.agents import interpreter
from app.voice.stt import MEDIA_TYPES, SarvamSTT, SpeechInput, from_env

def words(text):
    # Retain Malayalam vowel marks. A regex \w tokeniser would strip them.
    text = unicodedata.normalize("NFC", text).casefold()
    return "".join(c if unicodedata.category(c)[0] in "LMN" or c.isspace() else " "
                   for c in text if c not in "\u200c\u200d").split()


def word_error_rate(reference, transcript, *, reference_kind="verbatim"):
    if reference_kind != "verbatim":
        return {}  # English meaning is ground truth for semantics, not words.
    reference, transcript = words(reference), words(transcript)
    if not reference:
        raise ValueError("Reference is empty")
    previous = list(range(len(transcript) + 1))
    for i, actual in enumerate(reference, 1):
        row = [i]
        for j, recognised in enumerate(transcript, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (actual != recognised)))
        previous = row
    return {"word_edits": previous[-1], "reference_words": len(reference),
            "wer": previous[-1] / len(reference)}


def grade(output, expected):
    if expected is None:
        return None
    if output is None:
        return False
    return all(set(output[key]) == set(value) if key == "intents" else output[key] == value
               for key, value in expected.items())


def read_notes(directory, *, require_references=True):
    if not directory.is_dir():
        raise ValueError(f"Create {directory} and add original audio with same-name .txt transcripts.")
    extensions = {f".{extension}" for extension in MEDIA_TYPES.values()}
    notes = []
    for audio in sorted(directory.iterdir()):
        if audio.suffix.lower() not in extensions:
            continue
        reference = audio.with_suffix(".txt")
        reference_text = reference.read_text().strip() if reference.exists() else None
        if not reference_text and require_references:
            raise ValueError(f"Missing human transcript: {reference}")
        # Grade only against a human-reviewed expectation. A filename tells
        # us the planned scenario, not what was actually said in this take.
        expectation = audio.with_suffix(".json")
        expected = json.loads(expectation.read_text()) if expectation.exists() else None
        notes.append((audio, reference_text or None, expected))
    if not notes:
        raise ValueError(f"No voice notes in {directory}; real recordings and human transcripts are needed.")
    return notes


def markdown(rows):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["# Voice input evaluation", "",
             "Original recordings; no denoising or transcription repair. References, where present, are human-supplied. "
             "Missing ground truth is not inferred from the recognised text.", "",
             "WER is omitted for translated meaning references. For verbatim references it uses NFC, case folding and punctuation removal; no translation or number rewriting. "
             "Different scripts, loanword spelling and spoken-versus-digit numbers can increase WER without changing meaning.", "",
             "| Note | Ground-truth reference | Sarvam transcript | WER | STT confidence | Language probability (not used) |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        wer = f"{row['wer']:.1%}" if row.get("wer") is not None else "—"
        lines.append("| " + " | ".join(map(cell, [row["id"], row["reference"] or "Not supplied", row.get("transcript", "STT failed"),
                                                    wer, row.get("stt_confidence") if row.get("stt_confidence") is not None else "unavailable",
                                                    row.get("language_probability")])) + " |")
    lines += ["", "| Note | Interpreter on reference | Interpreter on STT | Reference correct | STT correct |",
              "|---|---|---|---|---|"]
    for row in rows:
        def reading(key):
            output = row.get(key)
            if not output:
                return "No reading"
            return f"{output['event_type']}; {','.join(output['intents'])}; wait={output['driver_claimed_wait_minutes']}"
        lines.append("| " + " | ".join(map(cell, [row["id"], reading("reference_interpretation"),
                                                    reading("transcript_interpretation"), row.get("reference_correct"),
                                                    row.get("transcript_correct")])) + " |")
    available = sum(row.get("stt_confidence") is not None for row in rows)
    lines += ["", f"Genuine STT confidence present: {available}/{len(rows)}. "
              "Without it, confidence/error correlation cannot be measured. Language probability is not a substitute.", "",
              "With verbatim references, a correct reference reading becoming a wrong STT reading exposes a regression between the inputs. "
              "With translated references, language is also different: that comparison alone cannot isolate transcription damage. "
              "An incorrect reference reading already exposes the interpreter boundary in SPEC 2.1. "
              "Correctness is ungraded without a human-reviewed .json expectation. Review the transcripts for negation, events and figures as well as WER. "
              "A handful of notes cannot establish general reliability.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=ROOT / "recordings/stt")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "recordings/stt-results")
    parser.add_argument("--allow-missing-references", action="store_true",
                        help="Transcribe and interpret available audio, leaving accuracy unmeasured where references are missing")
    parser.add_argument("--reference-kind", choices=["verbatim", "translation"],
                        help="Translation references allow semantic grading, but no word-error rate")
    args = parser.parse_args()
    try:
        notes = read_notes(args.directory, require_references=not args.allow_missing_references)
    except ValueError as error:
        parser.error(str(error))
    conditions_path = args.directory / "conditions.json"
    conditions = json.loads(conditions_path.read_text()) if conditions_path.exists() else {}
    reference_kind = args.reference_kind or conditions.get("reference_kind", "verbatim")
    if reference_kind not in {"verbatim", "translation"}:
        parser.error("Reference kind must be verbatim or translation")
    provider = from_env()
    if not isinstance(provider, SarvamSTT):
        parser.error("Set STT_PROVIDER=sarvam and SARVAM_API_KEY for this evaluation")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows, reference_readings = [], {}
    speech = SpeechInput(lambda: provider)
    tracing.start()
    metadata = {"at": datetime.now(timezone.utc).isoformat(), "provider": provider.name,
                "reference_kind": reference_kind, "recording_conditions": conditions,
                "stt_model": provider.model, "mode": "codemix", "interpreter_model": interpreter.cheap_model(),
                "interpreter_prompt_sha256": hashlib.sha256(interpreter.system_prompt().encode()).hexdigest()}
    original_response = provider.response
    raw = {}
    def capture(audio, media_type):
        raw.clear()
        raw.update(original_response(audio, media_type))
        return raw
    provider.response = capture
    for path, reference, expected in notes:
        audio = path.read_bytes()
        media_type = next(key for key, extension in MEDIA_TYPES.items() if path.suffix.lower() == f".{extension}")
        row = {"id": path.stem, "reference": reference, "reference_kind": reference_kind, "expected": expected,
               "audio_sha256": hashlib.sha256(audio).hexdigest(),
               "reference_sha256": hashlib.sha256(reference.encode()).hexdigest() if reference else None}
        raw.clear()
        with tracing.trace("voice evaluation", seed=str(uuid4()), input={"note": path.name}) as trace:
            def interpret(text):
                started = perf_counter()
                try:
                    return interpreter.interpret(text).model_dump(mode="json"), None, (perf_counter() - started) * 1000
                except Exception as error:
                    return None, type(error).__name__, (perf_counter() - started) * 1000
            if reference:
                if reference not in reference_readings:
                    reference_readings[reference] = interpret(reference)
                row["reference_interpretation"], row["reference_error"], _ = reference_readings[reference]
                row["reference_correct"] = grade(row["reference_interpretation"], expected)
            started = perf_counter()
            try:
                result = speech.transcribe(audio, media_type)
            except Exception as error:
                row["stt_error"] = type(error).__name__
            else:
                row.update(transcript=result.text, stt_confidence=result.stt_confidence,
                           language_probability=result.language_probability,
                           transcription=asdict(result), stt_latency_ms=(perf_counter() - started) * 1000,
                           **(word_error_rate(reference, result.text, reference_kind=reference_kind) if reference else {}))
                output, error, latency = interpret(result.text)
                row.update(transcript_interpretation=output, interpreter_error=error, interpreter_latency_ms=latency,
                           transcript_correct=grade(output, expected))
            trace.finish(lambda: {"output": row})
        rows.append(row)
        (args.output_dir / f"{path.stem}.sarvam.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
        (args.output_dir / "report.json").write_text(json.dumps({**metadata, "rows": rows}, ensure_ascii=False, indent=2) + "\n")
        (args.output_dir / "report.md").write_text(markdown(rows))
        print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)
    tracing.flush()
    print(f"Report: {args.output_dir / 'report.md'}")


if __name__ == "__main__":
    main()
