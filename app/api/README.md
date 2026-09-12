Run from the repository root:

```sh
uv run uvicorn app.api.main:app --reload
```

`/driver` shows the last message, reply, expandable cited claims, and this shift's
complete record. `/dispatcher` starts at 11:13. The clock slider, playback, and
moment links replay 08:00–18:00 without changing submitted reports or approvals.
Playback pauses briefly at key events, including the watchdog firings at 13:42
and 14:16. Current-shift `+1 min` / `+15 min` runs the watchdog on live state.

With `TTS_PROVIDER=sarvam` and `SARVAM_API_KEY`, the driver page fetches speech
after rendering the reply. The separate audio request synthesises only the
routed prose, using Bulbul v3, and plays it automatically. Replay reuses the
same audio; browser autoplay restrictions leave a tap-to-play control. Page
polls neither restart nor interrupt playback. Missing configuration or a failed
call (20-second socket timeout, no retry) leaves the reply as text. The cache,
including failed attempts, lasts for this process. A live reply's TTS span
uses its message trace as parent, even though that trace has already ended.

`STT_PROVIDER=sarvam` enables voice input with the same `SARVAM_API_KEY`.
Recording requests microphone access on click, stops at 25 seconds, and lets
him listen, save or discard before sending. Typed input stays alongside it.
The browser needs localhost or HTTPS. Original audio goes to Saaras v3 in
`codemix` mode; its returned transcript enters the same interpreter path,
without translation, repair or reference hints. Duplicate completed message
ids do not transcribe or record twice. Audio is not retained on the server.

Transcription failure leaves an explicit error and the typed box, with no
invented transcript or trip event. The STT span records provider, latency and
confidence under the message trace. Missing confidence is shown as unavailable
on the board and earns zero credit, including on a later live safety check.
The recogniser's language-detection probability is separate and unused in the
score. The adapter uses a 30-second socket timeout and no automatic retries.

For the voice evaluation, put original audio and verbatim same-name `.txt`
references in `recordings/stt/`. WAV or M4A, under 30 seconds each; WebM, OGG,
MP3, AAC and FLAC also work. Keep Malayalam in Malayalam script and English in
English where possible. `scripts/check_stt.py` compares normalised WER and the
interpreter's reading of the reference against its reading of Sarvam's text.
Results and raw provider responses are saved in `recordings/stt-results/`.
New recordings and results are ignored by git; the first six-note corpus and
its reports are explicitly committed. For translated meaning references use
`--reference-kind translation` to omit WER; `conditions.json` can also set
`reference_kind`. Suggested names `m06-quiet`, `m06-noisy`,
`m07-quiet`, `m07-noisy`, `m10-quiet`, `m10-noisy`, `arrival` label the planned
scenarios. An optional same-name `.json` file supplies human-reviewed expected
interpreter fields, for example `{"event_type":"SERVICE_STARTED","intents":["REPORT"]}`.
Without it, the two readings are displayed but correctness stays ungraded.
Do not score a reference against what the speaker was supposed to say.

Replay loads real responder and critic outputs from
`app/data/seed/replay_assessments.json`, captured against the seeded SOP index.
These are labelled captured model checks; interpretation is fixture-provided.
The fixture checksum prevents attaching captures to a changed event sequence.
No model or database is needed to view replay. To refresh captures with the
configured models and indexed SOP database:

```sh
uv run python scripts/capture_replay_assessments.py
```

Typed reports use `LITELLM_MODEL_CHEAP`. Exceptions, questions, and “Run live
safety check” also use the strong model, embedding settings, and indexed
Postgres SOPs from `.env.example`. Live checks replace the card assessment and
update the driver's reply. Every exception exposes its opening input, retrieved
passages, routing signals, decision, and action audit in its reasoning trace.

This demo holds reports and approvals in one process. Use one worker; restarting
resets them. Customer approval records a review and never sends a message,
approves a resolution, or writes precedent. There is no map, driver list,
ranking, or per-driver history.

htmx 2.0.10 is vendored under `static/vendor` with its Zero-Clause BSD licence.
