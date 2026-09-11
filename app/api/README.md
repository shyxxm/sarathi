Run from the repository root:

```sh
uv run uvicorn app.api.main:app --reload
```

`/driver` shows the last message, reply, expandable cited claims, and this shift's
complete record. `/dispatcher` starts at 11:13. The clock slider, playback, and
moment links replay 08:00–18:00 without changing submitted reports or approvals.
Playback pauses briefly at key events, including the watchdog firings at 13:42
and 14:16. Current-shift `+1 min` / `+15 min` runs the watchdog on live state.

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
