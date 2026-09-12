# Note 04 — ambiguity handling failed

Reference supplied earlier: “Over.” Meaning done, but genuinely unclear what is over. Sarvam: `കഴിഞ്ഞു.` Interpreter: `STOP_COMPLETED`, REPORT, legible=true, no unresolved fields. The same interpreter returned UNCLEAR on the English reference. The transcript supplies no completed activity, so stop completion is unsupported. This does not establish an ASR error; the exact spoken reference is unverified and the two inputs are in different languages.

Audio: 3.051 s, original 48 kHz mono AAC/M4A. STT: 0.668 s; interpreter: 1.592 s. Genuine STT confidence unavailable; language probability 0.946 is not a quality score. No trip event was applied during this evaluation. The initial sandbox-blocked attempt produced no transcript; the successful run is the measurement below.

# Voice input evaluation

Original recordings; no denoising or transcription repair. References, where present, are human-supplied. Missing ground truth is not inferred from the recognised text.

WER is omitted for translated meaning references. For verbatim references it uses NFC, case folding and punctuation removal; no translation or number rewriting. Different scripts, loanword spelling and spoken-versus-digit numbers can increase WER without changing meaning.

| Note | Ground-truth reference | Sarvam transcript | WER | STT confidence | Language probability (not used) |
|---|---|---|---|---|---|
| 4 | Over. | കഴിഞ്ഞു. | — | unavailable | 0.946 |

| Note | Interpreter on reference | Interpreter on STT | Reference correct | STT correct |
|---|---|---|---|---|
| 4 | UNCLEAR; REPORT; wait=None | STOP_COMPLETED; REPORT; wait=None | True | False |

Genuine STT confidence present: 0/1. Without it, confidence/error correlation cannot be measured. Language probability is not a substitute.

With verbatim references, a correct reference reading becoming a wrong STT reading exposes a regression between the inputs. With translated references, language is also different: that comparison alone cannot isolate transcription damage. An incorrect reference reading already exposes the interpreter boundary in SPEC 2.1. Correctness is ungraded without a human-reviewed .json expectation. Review the transcripts for negation, events and figures as well as WER. A handful of notes cannot establish general reliability.


## Finding classification — SPEC §2.4

Note 04 is recorded separately as **STT ambiguity collapse**, not an instance of §2.1 frame-completion. The speaker's diagnosis is that the recogniser resolved an ambiguity belonging to the driver: typed `Over.` yielded UNCLEAR, while voice yielded `കഴിഞ്ഞു.` and then STOP_COMPLETED with no unresolved fields. A clean-looking STT choice does not disclose the discarded ambiguity to the composite score, grounding or unresolved_fields. The restatement loop is the only current guard: the driver hears the concrete recorded completion and can correct it.

The exact spoken reference remains unverified. Different input languages, and the fact that `കഴിഞ്ഞു` does not name what finished, mean the outputs alone do not isolate the recogniser's contribution from the interpreter's. The evaluation tested neither trip mutation nor a driver correction. Read-back carries the safety burden across transcription errors, interpreter frame-completion and STT ambiguity collapse; no successful correction-loop trial is claimed.
