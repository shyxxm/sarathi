# Sarathi

A road-freight assistant built for the **driver**, not the office.

A lorry driver in Kerala reports by message in Malayalam, English, or both in
one sentence. Sarathi works out what he said, records it against his trip, and
answers him in his own language: what it recorded, what this customer's
standing instructions actually say, and whether his waiting time is being
counted. When it doesn't know, it says so and gets a person.

It also notices when he has gone quiet, and checks on him.

*Sarathi — the charioteer. The one who guides, not the one who watches.*

One simulated trip, typed or recorded messages, and optional spoken replies.
The [first six-note voice evaluation](recordings/stt-results/2026-09-12-six-notes/report.md)
found five expected readings and one failed ambiguity case. One speaker in
clean conditions; reliability on route-driver audio remains unmeasured.

## Who it's for

**The driver first.** He works in a language the software doesn't, with his
hands on a wheel, and his account of his own day depends on someone else
writing it down. When a gate is shut, he has no easy way to report it, no way
to find out what he's allowed to do, and no record showing he waited from 10:42
to 11:27.

**The dispatcher second.** They get a board that answers one question — *what
needs me?* — instead of sixty unread messages.

## Design commitments

An agent that listens to drivers all day is one configuration change away from
being a surveillance tool. Sarathi isn't one, and that is built into the code:

- **No driver scoring, ranking or performance history.** Exceptions attach to
  stops and trips, never to a person.
- **No location tracking.**
- **Silence gets a check-in, not a flag.** *"Everything alright?"* — never
  *"driver unresponsive."*
- **Records capture what happened, not who failed.** A timestamped gate closure
  is there to explain a delay, not to blame someone for it.
- **The driver is told what was recorded about him**, as it happens. Every reply
  restates it, so he can correct us in the next breath.
- **Nothing goes to a customer without human approval**, and nothing the system
  decided unsupervised is ever reused to justify the next decision.
- **No claim about his pay or liability without a cited rule.** If Sarathi can't
  cite one, it tells him it will find out.

## How it works

```
        driver message (Malayalam / English / mixed)
                          │
                 [ INTERPRETER ]            model: intent and meaning only.
                          │                 It has no field for an id.
                identity resolver           code: which trip, stop, customer
                          │
              ┌───────────┴───────────┐
           REPORT                 QUESTION
              │                       │
        state machine                 │     code: events, detention clock,
        exception rules               │     exceptions. No clock reads.
              └───────────┬───────────┘
                   [ RETRIEVER ]            pgvector: this customer's SOPs.
                          │                 The query is his words, nothing else.
                   [ RESPONDER ]            model: the reply, and each claim
                          │                 tied to the chunk it rests on
                 [ SAFETY CRITIC ]          computed signals, plus a model check:
                          │                 does this passage support this claim?
                     [ ROUTER ]             code: SPEAK / SPEAK_HEDGED / ESCALATE
                          │
   reply that restates the record  ·  dispatcher board  ·  customer drafts (approval only)

   watchdog (code, simulated clock) ──► "Everything alright?" check-ins
```

Language models do three jobs: reading the driver's message, drafting the
reply, and checking the reply's claims against the cited text. Everything
between them is plain code. `app/domain/` is pure: state in, values out, no
I/O, no model calls, no clock reads. That keeps the whole shift replayable
against a simulated clock and makes the core testable in about a second.

Every event carries two timestamps: when it happened, and when Sarathi learned
of it. Detention is billed from when Sarathi learned he had arrived. The
driver's own claimed wait is stored and read back to him, and never billed
from.

`SPEC.md` is the contract. `BRIEF.md` explains why the project exists.

## Running it

Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest                          # deterministic core, no network
uv run python scripts/replay_shift.py  # the seeded shift, zero model calls
uv run uvicorn app.api.main:app --reload
```

Open `/driver` and `/dispatcher`. The clock slider replays the seeded shift
from 08:00 to 18:00, using captured model outputs, with no API key and no
database.

To send your own messages, which calls real models:

```sh
cp .env.example .env         # set LITELLM_MODEL_CHEAP / _STRONG / _EMBEDDING and a key
docker compose up -d         # Postgres + pgvector
uv run python scripts/index_sops.py
```

Tracing is optional. Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in
`.env` (any `pk-lf-…` / `sk-lf-…` pair you choose), then:

```sh
docker compose --profile tracing up -d   # Langfuse on http://localhost:3100
```

That project and its keys are created on first start, in Langfuse's own
Postgres. Every driver message is one trace, found by its message id. If
Langfuse is down, Sarathi runs unchanged.

For spoken replies, set `TTS_PROVIDER=sarvam` and `SARVAM_API_KEY` in `.env`.
Text arrives first; audio plays when ready, with a replay button. Missing
settings or a failed TTS call leave the text reply working. Only the reply
prose is synthesised. Audio is cached by reply id until the app restarts.
If the browser blocks autoplay, tap Replay reply. Run the live m06 check with
`uv run python scripts/check_tts.py`; audio, text and timings go to
`/tmp/sarathi-m06`.

For voice input, also set `STT_PROVIDER=sarvam`. Record up to 25 seconds on
`/driver`, preview and send. Microphone access needs localhost or HTTPS. The
typed box stays available; a transcription failure submits no words. Sarvam
does not provide transcription confidence, so voice gets zero credit for that
safety signal, explicitly shown as unavailable. Language-detection probability
is never substituted. Put original voice notes and same-name human `.txt`
transcripts in `recordings/stt/`, then run `uv run python scripts/check_stt.py`.
Use `--reference-kind translation` for meaning-level references; those cannot
support word-error rates. The committed six-note corpus uses English meaning
references and records this setting in `conditions.json`.

Language models go through LiteLLM, so any provider works. The interpreter
calibration set runs against a local Ollama model by default, unmetered and
not a quality signal. Add `--score` to run it on the configured cheap tier:

```sh
uv run python scripts/check_interpreter.py [--score]
```

`app/api/README.md` covers the two surfaces in more detail.

## Deliberately not built

- **No fleet map, no driver leaderboard, no per-driver history view.** These
  are left out on purpose, not waiting on a later milestone.
- **No location tracking, no auth, no real WhatsApp or TMS.** Those integrations
  are simulated. The product is the understanding layer and the operational
  state.
- **Customer messages are never sent.** Approving a draft records a review and
  nothing else.
- **Not yet built:** corrections and precedent
  write-back (M7); a LangGraph port and FastMCP tools, as learning exercises
  (M8). v0.1 is one trip, five stops, text input.

## What building it found

These are the most interesting things in the repo. Each is written up in full
in `SPEC.md`, with the measurements.

### The model finishes the sentence it expects (SPEC 2.1)

```
m07   gate thurannu irakkan thudangi
      "the gate opened and unloading has started"
      read as: GATE_CLOSED
```

Every word arrived intact, and `thurannu` means *opened*. But *gate* is the
salient word, and a gate in a lorry driver's message is almost always a shut
one. The model completed that frame and reported the opposite of what he said.
No damage and no trip history were needed for it to happen.

The same mechanism defeats a missing negation. `ivide aarum illa` (*nobody is
here*) loses `illa` to transcription and becomes `ivide aaru`. It is still read
as a confident report that nobody is there, on a local 7B model and on Haiku,
with the rule written into the prompt five different ways. The frame survives
the damage, so the model never misses the one word the claim rested on.

**Transcription confidence cannot catch this**, because the words were heard
perfectly and then overridden. What catches it is the restatement: Sarathi
says *"recorded — gate closed at stop 2"* and the driver says *no, it opened.*
That loop was built for bad transcription. It turned out to catch bad
comprehension too, because it doesn't care why we got it wrong — only that we
say what we understood to the one person who knows.

This boundary is still open, and fragile. On 11 September a prompt paragraph
about something unrelated flipped m07 to the correct reading, 3 times out of 3.
The next change tried — opening the model's reply with a `{` — flipped it back,
3 out of 3. Nothing explains either.

### Similarity ordering is fragile, so it can't enforce the citation rule (SPEC 5.1)

Rule 7 says Sarathi makes no claim about pay without a cited rule. The first
design enforced it with a similarity floor: a chunk scoring above the floor
counted as a citation. Then a probe held out from calibration scored higher
than a real question:

```
0.640   "diesel price at the pump near Aluva"              off-topic
0.635   "nobody is answering, who does the driver call"    a real question
```

That is an inverted ordering, not a narrow margin, and no cut point survives
it. The cause was vocabulary drift: he asks about *waiting*, and the document
is headed *Detention*. Rewriting the SOPs fixed the ordering. It did not make
the ordering trustworthy.

> A wording change in a document — not in the code, not in the model, not in
> the threshold — moved noise above a real question and back again. Anything
> that can be inverted by an editor choosing *Detention* over *waiting* is not
> a safety mechanism.

So rule 7 is enforced by a **grounding check**. Before anything is spoken, a
separate model call asks of each claim about pay, liability or the customer's
terms: *does this passage actually support this sentence?* That is entailment,
not similarity, and its answer doesn't move when someone rewrites a heading.
The floor stays, but only as a noise filter.

The calibration history carries its own lesson: **do not fit the calibration
to its own test.** One probe — about *quarterly amortisation of goodwill* —
scored strangely high, and it was kept rather than swapped out. It turned out
to be pointing at the real defect, which is the next finding.

### Anything you add to every query is also in the noise (SPEC 6.1)

The retrieval query was the driver's words wrapped in the resolved stop's
JSON: ids, status and five ISO timestamps. The calibration probes were built by
different code and never got that wrapper. So the floor was measured on one
query shape and applied to another. Once both sides were built the same way,
one customer's worst real question scored *below its own measured noise*.

The fix was a deletion. The query is now his words and nothing else. On the
tightest customer, headroom went from +0.003 to +0.191. The general rule:

> Anything added to every query is also in every noise probe. It raises the
> measured baseline exactly as fast as it raises a real question, while pulling
> every query toward the same point. A floor is `baseline + margin`.
> **Boilerplate spends headroom and buys no discrimination.**

The test for anything proposed for the query: *would it be in the query if a
noise probe were the input?* The driver's words pass. The trip's timestamps
don't. Even the event type, one short and defensible English phrase, costs
about 0.04 of floor, and it stays out.

### When the failure is ours, say so (SPEC 2.2)

A clean English question — *"How much free waiting time does this customer
allow?"* — made the interpreter answer in prose instead of returning JSON. It
took the question as addressed to itself. The driver was told *"I couldn't
read your message. Please try again."* That was false: his words were fine. And
retrying could never help, because the failure happened every time.

Now a failure is never presented as illegibility. His raw text goes to a
dispatcher as a processing failure, and he is told the fault is ours. The
calibration script had hidden this too: a model answering in prose was counted
alongside a 429 as "provider error, not scored", so this whole class of failure
never reached the score.

Opening the model's reply with `{` stops the prose. It was tested and
declined, because it made *over* — which should get *over what?* — read as
small talk, 3 times out of 3. A loud, honest failure that reaches a dispatcher
beats quietly accepting an ambiguous message. SPEC 2.2 has both rows.

## Licence

MIT. See `LICENSE`. htmx is vendored under its own Zero-Clause BSD licence.
