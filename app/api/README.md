Run from the repository root:

```sh
uv run uvicorn app.api.main:app --reload
```

Open `/driver` for typed reports and `/dispatcher` for the board. The current
demo starts at 11:13, at the seeded gate closure. Advance its paused clock with
`+1 min` or `+15 min`. The 08:00–18:00 slider is an independent, read-only replay
of the existing fixtures. It never rewinds submitted reports or approvals.

Replay needs no model or database. Typed reports use `LITELLM_MODEL_CHEAP`.
Exceptions and questions also use the strong model, embedding settings and
indexed Postgres SOPs from `.env.example` (`uv run python scripts/index_sops.py`).
“Check reply & rules” runs the responder, critic and router for an open exception
and puts the result on both surfaces. Fixture events show “Not assessed” until
an actual check runs in the current shift.

This M4 demo holds reports, reply source snapshots and approvals in one process.
Use one worker; restarting resets them. Approval records a review and never sends
a customer message. It does not approve a resolution or write a precedent.

There is no map, driver list, ranking or per-driver history. Exceptions belong
to trips and stops; the driver's record covers this shift only.

htmx 2.0.10 is vendored under `static/vendor` with its Zero-Clause BSD licence.
The remaining assets are one hand-written stylesheet and a small script for
source disclosure, preserving disclosures during polling, and clock labels.
