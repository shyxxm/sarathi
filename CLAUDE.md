# CLAUDE.md — Sarathi

Solo open-source learning project. Read `SPEC.md` before writing code — it is
the contract and wins over this file and over the code. `BRIEF.md` is the why.

## What this is

A road-freight assistant whose primary user is the **driver**, not the office.
He reports by voice in Malayalam, English or a mix. Sarathi understands him,
records it against the trip, and **speaks back** in his language — telling him
what was recorded, what the customer's rules actually say, and whether his
waiting time is being counted.

It also notices when he has gone quiet, and checks on him.

## The ten hard rules

Not style preferences. Each one, if broken, either breaks the demo or turns the
project into the thing it was built not to be.

1. **This is not a monitoring tool.** No driver scoring, ranking, or
   performance history. No location tracking. Exceptions attach to stops and
   trips, never to a person. Silence produces *"everything alright?"*, never
   *"driver unresponsive."* If a feature would look at home on a fleet-manager
   dashboard, it does not belong here.

2. **Every reply restates what was understood.** Never a bare acknowledgement.
   Transcription of code-mixed Malayalam is unreliable, and the read-back is how
   the driver catches our mistakes. `DriverReply.restated_facts` is never empty.

3. **The model never resolves identity.** The interpreter extracts semantics
   and intent. Which driver, trip, stop, customer — all resolved by code from
   current state. A model emitting a `stop_id` is a bug.

4. **The model never mutates state.** It emits an event. The state machine
   applies it. No path from model output to a write that skips validation.

5. **Never call `datetime.now()` in `domain/`.** Every function takes `now`.
   The shift is replayable against a simulated clock. This is the rule most
   often broken by accident and most expensive to fix late.

6. **Two timestamps, always.** `occurred_at` is when it happened, `ingested_at`
   is when we learned. Detention bills from `ingested_at` of arrival. The
   driver's claimed wait is stored, spoken back, never billed from.

7. **No claim about the driver's pay or liability without a cited SOP.** If
   retrieval returns nothing relevant, Sarathi says it will find out and routes
   to a human. Telling a driver he is covered when he is not is the worst thing
   this system can do.

8. **Nothing reaches a customer without human approval.** Auto-executed: ledger
   writes, review timers, state transitions, replies to the driver. Never:
   outbound communication.

9. **Only human-approved resolutions become precedent.** Writing unreviewed
   decisions into the retrieval index lets the system cite its own mistakes.

10. **`domain/` is pure.** State machine, watchdog, exception rules, detention,
    identity resolution: state in, values out. No I/O, no network, no model
    calls, no clock reads. This is what makes the core testable in milliseconds
    with zero tokens.

## Layout

```
app/
  contracts/    enums.py  event.py  exception.py  reply.py  decision.py
  domain/       clock.py  state_machine.py  identity.py  watchdog.py
                exception_rules.py  detention.py  resolution.py
  agents/       interpreter.py  responder.py  decision_agent.py
                safety_critic.py  router.py  prompts/
  voice/        stt.py  tts.py
  retrieval/    sop_store.py  precedent_store.py  embed.py
  data/         models.py  repository.py  seed/
  api/          main.py  driver.py  dispatcher.py  templates/
  eval/         fixtures/  test_domain.py  test_golden.py
scripts/        replay_shift.py  seed_db.py
```

Dependencies point inward. `domain/` imports only from `contracts/`. `agents/`,
`voice/` and `api/` may import `domain/`. Never the reverse.

## Build order

Sequential. Do not start a milestone until the previous one runs.

- **M0** contracts, seed data, fixtures
- **M1** deterministic core — `replay_shift.py` produces a correct event log
  from fixture events, zero model calls
- **M2** interpreter + identity resolver, text input only
- **M3** retrieval + responder + safety critic + router; driver replies in text
- **M4** driver page + dispatcher board + clock slider
- **M5** **ship v1 publicly** — README, licence, a real commit history
- **M6** voice in and out
- **M7** questions, corrections, precedent write-back
- **M8** LangGraph port and FastMCP tools, as deliberate learning exercises

M1 must pass before any model is wired in. M5 is not optional and not last.

## Testing

`eval/fixtures/golden_shift.json` maps each seeded message to its expected
intent, event type and exception outcome. `pytest eval/` runs the whole
deterministic core in under a second with no network.

Any change to `domain/` runs the golden test before it counts as done.

## Working style

- Ask before adding a dependency. The stack is in `STACK.md` and it is closed.
- When `SPEC.md` is ambiguous, stop and ask. Do not invent a field.
- Prefer boring. Server-rendered HTML, no build step, no SPA, no component
  library.
- No README essays, no docstring padding, no architecture docs. `SPEC.md`
  exists.
- Small commits at milestone boundaries. The repo should be runnable at the end
  of each one.

## Voice and vocabulary

Use the real words in code, comments and UI: trip, stop, consignee, consignor,
LR, detention, free time, reattempt, gate-in, delivery window, dispatcher. Not
"task", "job", "user", "delivery item".

Driver-facing strings are written to be *heard*, not read. Short sentences,
concrete numbers, no jargon, no hedging filler. Sarathi is a colleague who knows
the paperwork, not a customer-service bot. It never apologises decoratively and
never says "I'm just an AI."
