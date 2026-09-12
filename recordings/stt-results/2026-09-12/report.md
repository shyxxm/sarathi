# Five real voice notes — 12 September 2026

**The five intended readings survived. This supports continuing a supervised prototype; it does not establish field reliability.**

One speaker, female, clean conditions. Route drivers are overwhelmingly male and the audio will be worse. Treat this as an optimistic clean-condition ceiling, not a measurement of route-driver performance. Note 04 (ambiguous “Over”) is absent and was not tested. There is no engine-noise comparison or opened-gate contrast in these five notes.

Sarvam Saaras v3, codemix mode; interpreter: Haiku 4.5 at the existing settings. Original 48 kHz mono M4A files were sent unchanged. One STT call and one interpreter call per recording, plus one interpreter call per reference. No prompt tuning or transcription repair.

The supplied English references are used as **meaning-level ground truth**, not verbatim Malayalam transcripts. All five expected intent/event readings matched on both the English references and the recognised text. This is 5/5 semantic matches on this small set, not 100% word accuracy. Word-error rate is not available; computing it across these languages would be misleading. The initial script calculated mismatched WER values; these were removed without repeating any model calls.

Note 01 preserved the claimed 40 minutes. Note 02 retained both absence and unanswered-phone negations and the request for help, but “shop കൂട്ടിയ പോലെയുണ്ട്” appears garbled against the supplied meaning “shop looks closed.” The interpreter still chose CONSIGNEE_ABSENT. The exact lexical error needs a verbatim reference or listening review; correct classification does not prove every word was right. Notes 03, 05 and 06 retained arrival, the waiting-pay question and completed unloading respectively. No incorrect event inversion or lost-negation classification appeared in this set.

Genuine transcription confidence: absent on all five. Language probability ranged from 0.524 to 0.968; it is not a transcription-quality score. Confidence/error correlation cannot be assessed. SPEC 5 therefore continues to give unavailable voice confidence zero credit, and the read-back remains essential.

STT latency (one sample per note):

| Note | Audio duration | STT latency | Language probability — not used in safety score |
|---|---:|---:|---:|
| 01 | 10.987 s | 1.963 s | 0.766 |
| 02 | 9.963 s | 1.491 s | 0.803 |
| 03 | 2.795 s | 1.043 s | 0.968 |
| 05 | 4.587 s | 1.056 s | 0.777 |
| 06 | 3.051 s | 0.997 s | 0.524 |

Mean STT latency: 1.31 s. This excludes interpretation, retrieval, response generation and TTS.

## Exact outputs

Original recordings; no denoising or transcription repair. References, where present, are human-supplied. Missing ground truth is not inferred from the recognised text.

WER is omitted for translated meaning references. For verbatim references it uses NFC, case folding and punctuation removal; no translation or number rewriting. Different scripts, loanword spelling and spoken-versus-digit numbers can increase WER without changing meaning.

| Note | Ground-truth reference | Sarvam transcript | WER | STT confidence | Language probability (not used) |
|---|---|---|---|---|---|
| 1 | The gate is shut, security told me to wait and come back later, I'm standing outside. It's been forty minutes. | ഗേറ്റ് അടച്ചിരിക്കുക security പറഞ്ഞ് wait ചെയ്യ് പിന്നെ വരാൻ ഞാൻ പുറത്ത് നിൽക്കുവാ 40 minute ആയി | — | unavailable | 0.766 |
| 2 | There's nobody here, they're not picking up the phone, the shop looks closed. What do I do now? | ഇവിടെ ആരുമില്ല phone എടുക്കുന്നില്ല shop കൂട്ടിയ പോലെയുണ്ട് ഇപ്പോൾ എന്ത് ചെയ്യും | — | unavailable | 0.803 |
| 3 | Reached. | എത്തി. | — | unavailable | 0.968 |
| 5 | Sir, will I get paid for this waiting? | Sir ഈ wait ചെയ്യുന്നതിന് പൈസ കിട്ടുമോ? | — | unavailable | 0.777 |
| 6 | Ok sir, finished unloading. | Okay Sir load ഇറക്കി കഴിഞ്ഞു. | — | unavailable | 0.524 |

| Note | Interpreter on reference | Interpreter on STT | Reference correct | STT correct |
|---|---|---|---|---|
| 1 | GATE_CLOSED; REPORT; wait=40 | GATE_CLOSED; REPORT; wait=40 | True | True |
| 2 | CONSIGNEE_ABSENT; REPORT,QUESTION; wait=None | CONSIGNEE_ABSENT; REPORT,QUESTION; wait=None | True | True |
| 3 | ARRIVED_STOP; REPORT; wait=None | ARRIVED_STOP; REPORT; wait=None | True | True |
| 5 | None; QUESTION; wait=None | None; QUESTION; wait=None | True | True |
| 6 | STOP_COMPLETED; REPORT; wait=None | STOP_COMPLETED; REPORT; wait=None | True | True |

Genuine STT confidence present: 0/5. Without it, confidence/error correlation cannot be measured. Language probability is not a substitute.

With verbatim references, a correct reference reading becoming a wrong STT reading exposes a regression between the inputs. With translated references, language is also different: that comparison alone cannot isolate transcription damage. An incorrect reference reading already exposes the interpreter boundary in SPEC 2.1. Correctness is ungraded without a human-reviewed .json expectation. Review the transcripts for negation, events and figures as well as WER. A handful of notes cannot establish general reliability.


## Follow-up

This is the historical five-note run. The speaker subsequently confirmed note 02's shop phrase is garbled; its exact verbatim reference remains pending (SPEC §2.1). Note 04 was added in a separate run and failed ambiguity handling, recorded as STT ambiguity collapse (SPEC §2.4). See the [six-note comparison](../2026-09-12-six-notes/report.md) for the current result: five expected readings and one failure.
