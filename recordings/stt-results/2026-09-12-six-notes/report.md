# Six real voice notes — 12 September 2026

**Five expected intent/event readings; note 04 fails ambiguity handling. This is not field-ready evidence.**

Note 04 was added after the original five-note evaluation. Sarvam returned `കഴിഞ്ഞു.` (finished/over). The interpreter supplied `STOP_COMPLETED`, marked it legible and left no unresolved fields. The supplied English reference `Over.` instead produced `UNCLEAR`, legible=true, with event_type unresolved. The speaker explicitly described the intended utterance as ambiguous about what was over. Stop completion is therefore an unsupported interpretation. This evaluation did not apply an event to a trip.

The supplied English references are meaning-level ground truth; exact spoken Malayalam references remain unverified. WER is unavailable, and the different-language comparison alone cannot isolate ASR damage. One STT and one interpreter call per audio; no prompt tuning or transcription repair. The network-blocked attempt for note 04 produced no transcript and is not an accuracy sample.

Note 02 is the first observed real-audio instance of the SPEC 2.1 frame-completion pattern: the speaker confirms that “shop കൂട്ടിയ പോലെയുണ്ട്” is garbled, yet the interpreter returned CONSIGNEE_ABSENT. Correct classification came despite a wrong transcript. Its exact verbatim reference remains pending. Note 04 now demonstrates an incorrect event reading as well. The restatement loop must expose what was recorded; its ability to elicit a correction was not tested here.

Single female speaker, clean conditions: an optimistic ceiling, not a measurement of route-driver performance. Route drivers are overwhelmingly male and audio will be worse. Engine noise and the opened-gate contrast remain untested. Genuine STT confidence is unavailable on all six; language probability is not transcription quality and cannot be used to assess confidence/error correlation.


| Note | Supplied reference | Exact Sarvam transcript | Interpreter on reference | Interpreter on transcript | STT latency |
|---|---|---|---|---|---|
| 1 | The gate is shut, security told me to wait and come back later, I'm standing outside. It's been forty minutes. | ഗേറ്റ് അടച്ചിരിക്കുക security പറഞ്ഞ് wait ചെയ്യ് പിന്നെ വരാൻ ഞാൻ പുറത്ത് നിൽക്കുവാ 40 minute ആയി | GATE_CLOSED; REPORT; wait=40 min | GATE_CLOSED; REPORT; wait=40 min | 1.963 s |
| 2 | There's nobody here, they're not picking up the phone, the shop looks closed. What do I do now? | ഇവിടെ ആരുമില്ല phone എടുക്കുന്നില്ല shop കൂട്ടിയ പോലെയുണ്ട് ഇപ്പോൾ എന്ത് ചെയ്യും | CONSIGNEE_ABSENT; REPORT, QUESTION | CONSIGNEE_ABSENT; REPORT, QUESTION | 1.491 s |
| 3 | Reached. | എത്തി. | ARRIVED_STOP; REPORT | ARRIVED_STOP; REPORT | 1.043 s |
| 4 | Over. | കഴിഞ്ഞു. | UNCLEAR; REPORT | STOP_COMPLETED; REPORT | 0.668 s |
| 5 | Sir, will I get paid for this waiting? | Sir ഈ wait ചെയ്യുന്നതിന് പൈസ കിട്ടുമോ? | None; QUESTION | None; QUESTION | 1.056 s |
| 6 | Ok sir, finished unloading. | Okay Sir load ഇറക്കി കഴിഞ്ഞു. | STOP_COMPLETED; REPORT | STOP_COMPLETED; REPORT | 0.997 s |

Mean STT latency: 1.203 s, excluding downstream processing. Correct semantic readings: 5/6; this is not word accuracy or a field reliability estimate.


## Finding classification — SPEC §2.4

Note 04 is recorded separately as **STT ambiguity collapse**, not an instance of §2.1 frame-completion. The speaker's diagnosis is that the recogniser resolved an ambiguity belonging to the driver: typed `Over.` yielded UNCLEAR, while voice yielded `കഴിഞ്ഞു.` and then STOP_COMPLETED with no unresolved fields. A clean-looking STT choice does not disclose the discarded ambiguity to the composite score, grounding or unresolved_fields. The restatement loop is the only current guard: the driver hears the concrete recorded completion and can correct it.

The exact spoken reference remains unverified. Different input languages, and the fact that `കഴിഞ്ഞു` does not name what finished, mean the outputs alone do not isolate the recogniser's contribution from the interpreter's. The evaluation tested neither trip mutation nor a driver correction. Read-back carries the safety burden across transcription errors, interpreter frame-completion and STT ambiguity collapse; no successful correction-loop trial is claimed.
