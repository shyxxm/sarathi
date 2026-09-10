# Sarathi — Prompts

Copy-paste, one milestone at a time. `SPEC.md`, `CLAUDE.md` and `STACK.md` live
in the repo root and both tools read them from M0 onward.

**Claude Code** takes work carrying judgment: prompts, schemas, the responder's
voice, exception rules, reviewing against the spec.
**Codex** takes work the spec fully determines: models, repository, seeding,
templates, wiring.

Cross-check at each milestone boundary. Two agents against one spec still
drift.

---

## M0 — Contracts and seed

### → Claude Code

```
Read SPEC.md, CLAUDE.md and STACK.md in full before writing anything.

Create app/contracts/ implementing exactly SPEC section 3: enums.py, event.py,
exception.py, reply.py, decision.py. Pydantic v2.

Constraints:
- No fields beyond what SPEC lists. No business logic, no helper methods.
- The interpreter output model (SPEC 3.2) is separate from OperationalEvent and
  must be structurally incapable of holding driver_id, vehicle_id, trip_id or
  stop_id.
- DriverReply.restated_facts is required and non-empty. Enforce it in the model.

Then app/domain/clock.py: a Clock protocol with now(), a SimulatedClock that
advances across a shift window, and a FrozenClock for tests. Nothing in domain/
reads time except through an injected Clock.

Stop there. I will review before you continue.
```

### → Codex

```
Read SPEC.md and CLAUDE.md.

Create app/data/seed/ as JSON:

customers.json  3 customers. id, name, free_detention_minutes,
                detention_rate_paise_per_min, reattempt_cutoff_time,
                site_contact, gate_procedure, delivery_window_policy.
                Vary the terms meaningfully - the demo depends on the rules
                differing between customers.
vehicles.json   1 vehicle, realistic Kerala registration.
drivers.json    1 driver, preferred_language field.
trips.json      1 trip, 5 stops, shift 08:00-18:00. Each stop: id, seq,
                customer_id, planned_arrival, planned_departure,
                service_minutes, window_open, window_close.

Then app/data/models.py (SQLAlchemy 2.0) mirroring SPEC section 3, plus tables
for messages (id, driver_id, occurred_at, ingested_at, raw_transcript,
audio_path, stt_confidence) and detention_ledger and replies.

Then app/data/repository.py: plain load/save functions. No ORM session leaks
above this layer.

No business logic. No state machine.
```

---

## M1 — Deterministic core, zero model calls

### → Claude Code

```
Implement app/domain/ per SPEC section 4:

state_machine.py    apply(state, event, now) -> (new_state, applied | rejected)
identity.py         resolve(message, state) -> identity fields + unresolved list
watchdog.py         tick(state, now) -> list[OperationalEvent]
exception_rules.py  evaluate(state, event, now) -> list[OperationalException]
detention.py        SPEC 4.2 exactly
resolution.py       SPEC 4.4 closure matching

All pure. State in, values out. `now` always an argument. No I/O, no clock
reads, no model calls.

Watchdog rules fire once per (stop, rule) unless the condition clears and
recurs - show me how you track that.

Illegal transitions must not mutate state. Return them as rejected with a
reason string that is usable in a driver-facing reply.
```

### → Codex

```
Write scripts/replay_shift.py.

Loads seed data, walks a SimulatedClock 08:00 to 18:00 in 1-minute steps,
applies fixture events from app/eval/fixtures/fixture_events.json at their
ingested_at, calls watchdog.tick() each step, prints a timestamped log.

Expected shape:

08:10  TRUCK_01 -> STOP_01  ARRIVED
10:13  EXCEPTION OPENED  GATE_CLOSED  stop=S02  (driver)
10:43  DETENTION_CROSSED  billable=1min
11:04  EXCEPTION RESOLVED  GATE_CLOSED
13:55  EXCEPTION OPENED  STOP_OVERDUE  stop=S04  (watchdog)

Zero LLM calls in this script.

Also write app/eval/fixtures/fixture_events.json: hand-written events with
identity already populated, covering four scenarios - a clean run, gate closed
with detention, consignee absent, and a stop that goes overdue silently.
```

**Gate:** this must produce a correct log and `pytest eval/` must pass before
any model is wired in.

---

## M2 — Interpreter

### → Claude Code

```
Build app/agents/interpreter.py per SPEC 3.2.

Input: transcript text only. Output: intents (a set, not one value), language,
event_type, question_text, location_hint, driver_claimed_wait_minutes,
contradicts_recent_state, transcript_legible, unresolved_fields.

Prompt in app/agents/prompts/interpreter.md as a separate file. Include 8
few-shot examples from our real message set covering: pure noise, Malayalam-
English mix, an implicit exception with no explicit problem statement, a message
that is both a report and a question, a correction of something we recorded
wrong, a garbled transcript, and one genuinely ambiguous case that must return
UNCLEAR.

Assume transcripts are unreliable - see SPEC section 2. The interpreter should
recognise a garbled transcript rather than confabulate structure from it.

Then wire app/domain/identity.py in after it. If location_hint contradicts the
expected stop, append "stop_id" to unresolved_fields. Never guess.

Run the whole message set through it and show me a table:
message -> extracted intents + event_type -> expected. Flag mismatches.
Show me the failures, not a summary.
```

---

## M3 — Retrieval, responder, safety critic

### → Codex

```
Build app/retrieval/ on pgvector.

sop_chunks: chunked per-customer SOP text, embedded, filtered by customer_id.
precedents: resolved exceptions, but ONLY where a human marked the resolution
approved. Enforce that in the store, not at the call site.

Retrieval: top-3 each, query from intent + event_type + customer_id + stop.

Write the SOP markdown for the 3 seeded customers, drawn from the terms already
in customers.json. Make them read like real standing instructions - specific,
slightly bureaucratic, occasionally inconsistent between customers.
```

### → Claude Code

```
Build app/agents/responder.py, safety_critic.py and router.py per SPEC 3.4 and
section 5.

Responder produces a DriverReply. Rules:
- restated_facts always populated, concrete, with times and numbers
- written to be HEARD - short sentences, no jargon, no decorative apology
- in the driver's language, matching the register he used
- any claim about pay or liability carries a cited SOP or is not made

Safety critic computes the four signals in code per SPEC 5. The model's own
rating is one input with weight capped at 0.20. Do not let the prompt decide
the routing.

Router applies the table plus the two hard overrides. ESCALATE still speaks -
it tells the driver a person is looking at it.

Before wiring it up, show me the assembled context for one gate-closed
exception. I want to read exactly what the responder sees.
```

---

## M4 — Surfaces

### → Codex

```
FastAPI app in app/api/. Jinja2 + htmx, polling. No React, no build step, no
CSS framework - one hand-written stylesheet.

Driver page (primary, phone-shaped): a big record/send control, the transcript
of what was heard, Sarathi's reply with text beside it, and a panel listing
everything recorded about today so far in plain language.

Dispatcher page (secondary): three columns NEEDS ATTENTION / HANDLED / RUNNING
FINE, an approval queue for drafted customer messages, and a clock slider
replaying 08:00-18:00. Exception cards show both the system-observed and
driver-claimed wait, with the free allowance and billable exposure.

No map. No driver list. No per-driver history. Their absence is deliberate -
see CLAUDE.md rule 1.
```

---

## M5 — Ship v1

### → Claude Code

```
Review the whole repo against SPEC.md and CLAUDE.md. Give me every violation
you find, ordered by how much it would matter to a real driver using this.
Be blunt. Do not fix anything yet.

Then write README.md: what it is, who it's for, the design commitments from
BRIEF.md, architecture in one diagram, how to run it, and what is deliberately
not built. Under 400 words before the run instructions.
```

Then: licence, public repo, tag `v0.1`. Do not start M6 until this is done.

---

## M6-M8 — Later

Voice in and out. Questions and corrections as their own paths. Precedent
write-back on approval. Then the LangGraph port and FastMCP tools as deliberate
exercises, with a note in the README about what each one actually changed.

---

## Validation checkpoints

Paste back to me, in order:

1. `app/contracts/` after M0 — everything binds to this
2. The interpreter results table from M2, failures included
3. The assembled responder context from M3
4. The M5 violation list

Contracts first. Nothing gets built on top of a wrong field.
