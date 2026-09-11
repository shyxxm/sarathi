# Sarathi — Spec

Track 02 · Assistive, Accessible & Inclusive Tech. Solo open-source build.

This file is the contract. If code and this file disagree, this file wins.
`BRIEF.md` is why. This is what.

---

## 1. The shape of it

```
              driver voice note (Malayalam / English / mixed)
                              |
                            [STT]
                              |
                     [1 INTERPRETER]  intent + semantics only
                              |
                    Identity Resolver   deterministic
                              |
              +---------------+---------------+
              |                               |
          is REPORT                       is QUESTION
              |                               |
       State Machine                          |
       Exception Eval                         |
     (deterministic)                          |
              |                               |
              +---------------+---------------+
                              |
                      [2 RETRIEVER]  customer SOP + precedent
                              |
              +---------------+---------------+
              |                               |
      [3 RESPONDER]                    [4 DECISION]
   what to say to driver            what ops actions to take
              |                               |
              +---------------+---------------+
                              |
                     [5 SAFETY CRITIC]  confidence + risk
                              |
                        [6 ROUTER]
                              |
        +---------------------+---------------------+
        |                     |                     |
     SPEAK              SPEAK + HEDGE          ESCALATE
   confident         "I think... let me      human answers,
                      confirm with office"   driver is told so
        |                     |                     |
        +---------------------+---------------------+
                              |
                           [TTS]
                              |
                          driver hears it
```

Nodes 1, 3, 4, 5 are model calls. Everything between them is plain code.

A message can be a report, a question, or both. *"Gate closed, can I leave?"*
is both. The interpreter returns intent as a set, not a single value.

---

## 2. The transcription problem, and why the reply solves it

Malayalam ASR word error rates are high, and code-mixed speech over engine
noise is worse. Assume the transcript is wrong some of the time.

**The spoken confirmation is the correction mechanism.** Every reply restates
what Sarathi understood in concrete terms — the stop, the event, the time, the
clock. A driver hearing *"recorded, gate closed at stop two, waiting counted
from 10:42"* can immediately say *"no, not gate, the consignee isn't here."*

Design consequences, all load-bearing:

- Replies always restate the understood facts, never just acknowledge.
- Any message that contradicts recent state is treated as a **correction**
  first and a new event second.
- `CORRECTION` is a first-class intent that reopens and amends the last event.
- Low transcription confidence lowers the composite score (§5) and pushes the
  reply toward hedging, not toward silent guessing.

**Legibility is not binary.** Between a clean transcript and an unreadable one
there is a wide recoverable band — syllables dropped, English words landing as
Malayalam near-homophones — where the meaning survives anyway. The interpreter
reads that band and reports what it says. It does not refuse it. Refusing
everything short of clean would make Sarathi useless on the audio it will
actually get, which is most of it.

What separates the bands is whether the meaning survives, not how many words
are damaged:

| Transcript | Band |
|---|---|
| `ivide aarum illa phone edukkunilla ... ippo enthu cheyyum` | clean |
| `ivide aar illa pon edukkunil ... ipp enthu cheyy` | damaged, meaning intact |
| `ivide aaru pon edukkunu ... ipp entho cheyth` | destroyed — `illa` is gone |

The middle row is missing syllables in nearly every word and still says plainly
that nobody is there and that he wants to know what to do. The third has lost
one word, `illa`, and with it the fact that anybody is absent. **Degradation
that preserves meaning is not illegibility.**

The middle band is handled by the composite score in §5, not by the interpreter
refusing to answer. A damaged transcript lowers the legibility signal, which
lowers confidence, which pushes the reply toward hedging — and a hedged reply
still restates what was understood, so the driver still gets the chance to
correct it. That is the mechanism. Silence is not.

### 2.1 A stated boundary

**The interpreter completes frames from context, and will override the
individual words to do it.** Damage is not required. This is the failure this
system has to survive, and it is a property of how the model reads, not of how
bad the audio was.

The clearest case has no corruption in it at all:

```
m07   gate thurannu irakkan thudangi
      the gate opened and unloading has started
      read as: GATE_CLOSED
```

Every word arrived intact. `thurannu` means opened. But `gate` is the salient
word, and a gate in a lorry driver's message is overwhelmingly a gate that is
shut — that is the frame, and the model completed it and reported the opposite
of what he said. Note that it needs no history to do this: the interpreter sees
one transcript and nothing else. The pull comes from the language, not from
anything it knows about the trip.

The same mechanism, with damage, explains why a missing morpheme cannot be
caught either:

```
m10g   ivide aar illa  pon edukkunil chaap pootti pol und ipp enthu cheyy
m10gg  ivide aaru      pon edukkunu  chaap poo    pol und ipp entho cheyth
```

These differ in one word. `illa` is the negation; in `m10gg` it is gone, and
with it the claim that anybody is absent. Both were read as a confident report
of absence, on a local 7B and on Haiku, with the rule written into the prompt
five different ways. A shop, a phone, nobody answering, a driver asking what to
do — that frame survives the corruption, so the model completes it. The one
morpheme carrying the negation is exactly what a context-completing reader does
not need, and therefore does not miss.

**`stt_confidence` does not catch this class.** m07's audio was clean and its
confidence will be high. The signal describes how well the words were heard,
and here the words were heard perfectly and then overridden. So the composite
score in §5 cannot be the only mitigation — it will score this reading as
trustworthy, because by every signal it has, it is.

**What actually catches it is §2.** Sarathi says *"recorded — gate closed at
stop 2, waiting counted from 10:42"* and the driver says *no, it opened, I'm
unloading.* The restatement loop was built for bad transcription and it turns
out to catch bad comprehension too, because it does not care why we got it
wrong — only that we say what we understood, in concrete terms, to the one
person who knows. §2 was already the answer to this. 2.1 simply did not
realise, at first, that it was pointing back at it.

Two things follow, and they are not alternatives:

- **`restated_facts` is load-bearing safety, not courtesy.** A reply that
  acknowledges without restating removes the only check on this failure. This
  is why `DriverReply.restated_facts` is never empty (§3.4, CLAUDE.md rule 2).
- **M6 still owes per-segment STT confidence.** It catches the damaged subclass
  — the segment carrying `illa` is the one the recogniser was least sure of, and
  that doubt has to reach §5 rather than being averaged away. It is necessary
  and it is not sufficient.

This is the single most important section in the file. A pipeline that assumes
good transcripts will feel broken to a real driver in the first minute.

---

## 3. Contracts

### 3.1 Enums

```python
class Language(str, Enum):
    ML = "ml"; EN = "en"; MIXED = "mixed"; HI = "hi"

class Intent(str, Enum):
    REPORT = "REPORT"          # something happened
    QUESTION = "QUESTION"      # driver wants to know something
    CORRECTION = "CORRECTION"  # driver is fixing what we recorded
    CHITCHAT = "CHITCHAT"      # "ok sir" — acknowledge, record nothing

class EventType(str, Enum):
    # progress
    DEPARTED = "DEPARTED"          # depot or stop — apply() knows which
    ARRIVED_STOP = "ARRIVED_STOP"
    SERVICE_STARTED = "SERVICE_STARTED"
    STOP_COMPLETED = "STOP_COMPLETED"
    # problems
    GATE_CLOSED = "GATE_CLOSED"
    CONSIGNEE_ABSENT = "CONSIGNEE_ABSENT"
    VEHICLE_BREAKDOWN = "VEHICLE_BREAKDOWN"
    DOCUMENT_ISSUE = "DOCUMENT_ISSUE"
    SHORTAGE_OR_DAMAGE = "SHORTAGE_OR_DAMAGE"
    DELIVERY_REFUSED = "DELIVERY_REFUSED"
    # non-events
    ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"
    UNCLEAR = "UNCLEAR"
    # system-generated only
    STOP_OVERDUE = "STOP_OVERDUE"
    DRIVER_SILENT = "DRIVER_SILENT"
    WINDOW_AT_RISK = "WINDOW_AT_RISK"
    DETENTION_CROSSED = "DETENTION_CROSSED"
    REATTEMPT_SCHEDULED = "REATTEMPT_SCHEDULED"  # emitted by SCHEDULE_REATTEMPT

class StopStatus(str, Enum):
    PENDING = "PENDING"; EN_ROUTE = "EN_ROUTE"; ARRIVED = "ARRIVED"
    IN_SERVICE = "IN_SERVICE"; COMPLETED = "COMPLETED"
    FAILED = "FAILED"; REATTEMPT_SCHEDULED = "REATTEMPT_SCHEDULED"

class ExceptionStatus(str, Enum):
    OPEN = "OPEN"; ACTING = "ACTING"; MONITORING = "MONITORING"
    RESOLVED = "RESOLVED"; EXPIRED = "EXPIRED"

class ResolutionStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"   # human confirmed — ONLY these become precedent
    REJECTED = "REJECTED"

class ReplyMode(str, Enum):
    SPEAK = "SPEAK"                # confident, stated plainly
    SPEAK_HEDGED = "SPEAK_HEDGED"  # answered, uncertainty voiced
    ESCALATE = "ESCALATE"          # human will answer; driver told so
```

### 3.2 Interpreter output (model, no state access)

```json
{
  "intents": ["REPORT", "QUESTION"],
  "language": "mixed",
  "event_type": "GATE_CLOSED",
  "question_text": "can I leave and come back?",
  "location_hint": "second godown",
  "driver_claimed_wait_minutes": 40,
  "contradicts_recent_state": false,
  "transcript_legible": true,
  "unresolved_fields": []
}
```

It must be **structurally incapable** of returning `driver_id`, `trip_id`,
`stop_id` or `vehicle_id`. Identity is code's job.

### 3.3 OperationalEvent

```python
@dataclass(frozen=True)
class OperationalEvent:
    id: str
    source: Literal["driver", "system"]
    occurred_at: datetime      # when it happened, shift time
    ingested_at: datetime      # when we learned. Clocks bill from this.
    event_type: EventType

    # from the model
    raw_transcript: str | None = None
    audio_path: str | None = None
    language: Language | None = None
    location_hint: str | None = None
    driver_claimed_wait_minutes: int | None = None

    # from code. NEVER model-populated.
    driver_id: str | None = None
    vehicle_id: str | None = None
    trip_id: str | None = None
    stop_id: str | None = None

    unresolved_fields: list[str] = field(default_factory=list)
    supersedes_event_id: str | None = None   # set by CORRECTION
    source_message_id: str | None = None     # idempotency
```

### 3.4 DriverReply

```python
@dataclass
class Claim:
    text: str                        # one assertion, checkable on its own
    cited_chunk_id: str              # the chunk it rests on

@dataclass
class ResponderOutput:               # the model boundary. no mode.
    language: Language
    text: str
    restated_facts: list[str]
    claims: list[Claim]
    confidence: float                # §5 caps its weight at 0.20

@dataclass
class DriverReply:                   # what the router assembles
    mode: ReplyMode
    language: Language
    text: str                       # in the driver's language
    restated_facts: list[str]        # what we understood — always populated
    claims: list[Claim]              # what survived grounding
    cited_sop_ids: list[str]         # derived from claims, never carried beside them
    audio_path: str | None = None
```

`restated_facts` is not optional and not decorative. It is §2.

**`claims` is what makes §5.1 possible.** A reply that returns one paragraph
with a citation stapled to it cannot be grounded: there is no unit small enough
to ask *does this passage support this sentence* about. So the responder emits
the assertions it is making individually, each already attributed to the chunk
it rests on, and the critic checks them one at a time.

The two lists are not the same kind of thing and only one of them is
grounding's business. `restated_facts` comes from state — the stop, the event,
the clock — and is checked by the driver himself when he hears it back (§2).
`claims` comes from retrieval, asserts what the customer's rules say, and is
checked against the chunk text before it may be spoken. Facts are never
grounded against a SOP and claims are never taken on trust.

`cited_sop_ids` is derived from `claims`, not carried alongside them, so the
reply cannot list a citation that no sentence in it rests on. That invariant is
enforced on the contract. It is the structural half of rule 7 — the half a
model cannot talk its way past — and the grounding check is the other half.

The responder never sets `mode`. `ResponderOutput` has no field for it, so a
prompt cannot route itself; §5 decides in code.

### 3.5 Decision (ops side)

```json
{
  "actions": [
    {"type": "LOG_DETENTION", "payload": {"billable_minutes": 1}},
    {"type": "NOTIFY_DISPATCHER", "payload": {"reason": "..."}}
  ],
  "rationale": "one or two plain sentences",
  "review_in_minutes": 20,
  "cited_sop_ids": ["SOP-ABC-DETENTION"]
}
```

Allowed actions: `LOG_DETENTION`, `NOTIFY_DISPATCHER`, `SCHEDULE_REATTEMPT`,
`SET_REVIEW_TIMER`, `DRAFT_CUSTOMER_MESSAGE`, `NO_ACTION`.

`DRAFT_CUSTOMER_MESSAGE` never sends. It queues for human approval, always.

### 3.6 OperationalException

@dataclass
class OperationalException:
    id: str
    trip_id: str
    stop_id: str | None
    exception_type: EventType

    opened_at: datetime
    opened_by: Literal["driver", "system"]
    opening_event_id: str

    status: ExceptionStatus
    resolution_status: ResolutionStatus = ResolutionStatus.PENDING

    # set by the safety critic / router, null until decided
    confidence: float | None = None
    risk: Literal["LOW", "HIGH"] | None = None
    reply_mode: ReplyMode | None = None

    cost_exposure_paise: int = 0
    review_due_at: datetime | None = None
    driver_informed: bool = False

    resolved_at: datetime | None = None
    resolving_event_id: str | None = None
    resolution_note: str | None = None

    decision: Decision | None = None
    audit: list[dict] = field(default_factory=list)

`opened_by` is load-bearing: the closing metric splits exceptions into
driver-reported versus watchdog-detected, and the watchdog ones are the point.

`driver_informed` is what stops the system silently accumulating a record about
someone it never spoke to. Any exception that reaches MONITORING with
`driver_informed` false is a bug, not a state.

`resolution_status` gates precedent write-back (§6, CLAUDE.md rule 9). Nothing
enters the retrieval index on PENDING.

`cost_exposure_paise` is recomputed from state, never accumulated in place —
detention maths lives in `domain/detention.py` and this field caches its output
for display.

---

## 4. Deterministic core

### 4.1 State machine

```
                          +---------------------------+
                          |                           v
PENDING -> EN_ROUTE -> ARRIVED -> IN_SERVICE -> COMPLETED
                          |            |
                          +------------+--> FAILED -> REATTEMPT_SCHEDULED
```

`SERVICE_STARTED` is optional. A driver who says *"delivered, leaving"* after
*"reached"* is not making a mistake, and rejecting him teaches him the system is
pedantic — which is how a driver-first tool loses its user in week one.

`PENDING -> ARRIVED` stays illegal. That one almost always means we have the
wrong stop, and asking which stop he means is the right answer.

`REATTEMPT_SCHEDULED` reaches state as an event, emitted when the
`SCHEDULE_REATTEMPT` action executes — not as a second way to write state. It
therefore appears in the driver's own record of the day like everything else.

**`reattempt_cutoff_time` is carried, not enforced.** `CustomerTerms` holds it
and the seed sets one per customer — 16:00, 17:00, 14:00 — but nothing in
`domain/` reads it. No rule today refuses a `SCHEDULE_REATTEMPT` past the
cutoff, or moves it to the next working day.

Until it is enforced, that customer's SOP is the only place the cutoff is
stated, so the driver hears it from retrieval or he does not hear it. That is
the wrong way round — a time the system already knows should not be a time only
a document remembers — and it is a rule for `domain/`, not a claim for the
responder to make. **M7.**

**`DEPARTED` is one event, not two.** *"Eranganu"* — I've set off — is the same
sentence whether he is leaving the depot or leaving stop 3, and nothing in the
words tells them apart. Only the current stop status does, and the interpreter
is not allowed to look at state (rule 3). So the model says `DEPARTED` and
`apply()` decides what it meant: a stop still `PENDING` means he is leaving the
depot and that stop goes `EN_ROUTE`; a stop already finished means he is
leaving it and the next one goes `EN_ROUTE`. Anything else is rejected and he
is asked.

This is the general test for anything in `EventType`: **if two identical
sentences map to different types depending on trip state, it is not one type
the model can choose.** Split it in code, not in the model's vocabulary.

Illegal transitions do not mutate state. They become `UNCLEAR` and push the
reply toward asking the driver what he meant.

### 4.2 Detention

```
arrival_observed_at = ingested_at of first ARRIVED_STOP at that stop
observed_wait       = now - arrival_observed_at
billable_minutes    = max(0, observed_wait - customer.free_detention_minutes)
exposure_paise      = billable_minutes * customer.detention_rate_paise_per_min
```

Driver-claimed wait is stored and shown, never billed from. Both numbers appear
on the dispatcher card and the claimed figure is what Sarathi acknowledges back
to the driver — *"you said about forty minutes; I have you arriving at 10:42"* —
so a discrepancy surfaces immediately instead of becoming a dispute later.

**The waiting ends when unloading starts**, at the `ingested_at` of
`SERVICE_STARTED` — or of `DEPARTED` where service never started at all, a
failed stop or a reattempt. Detention is time spent waiting, not time spent at
the site. Billing to departure would charge the customer for the driver's own
unloading.

`now` is still what the formula above takes, because a stop still in progress
has no end yet and the dispatcher card has to show the wait running. Where the
waiting has ended, the ledger freezes there.

**The ledger is written by the `LOG_DETENTION` action and by nothing else.** The
watchdog notices the crossing and says so; it does not write a row. One write
path, so the billed figure and the record of how it was reached cannot
disagree.

### 4.3 Watchdog

| Rule | Condition | Emits | Driver-facing |
|---|---|---|---|
| Overdue | `now > planned_arrival + grace`, status PENDING/EN_ROUTE | `STOP_OVERDUE` | check-in |
| Silence | `now - last_driver_event > 90min` | `DRIVER_SILENT` | check-in |
| Window risk | `projected_arrival > window_close` | `WINDOW_AT_RISK` | heads-up |
| Detention | `observed_wait > free_minutes` | `DETENTION_CROSSED` | reassurance |

`grace` is 15 minutes and the silence threshold is 90. Both live in
`domain/constants.py`; neither is tunable per driver, and never will be.

Every rule fires once per (stop, rule) unless the condition clears and recurs.

Clearing is not closing. `DETENTION_CROSSED` closes as an exception when service
starts (§4.4), but the arithmetic has not changed — the wait still exceeds the
free time. Only a fresh `ARRIVED_STOP` at that stop restarts the wait, so only
that re-arms the rule.

**A crossing is a fact about a problem, not a second problem.** If an exception
is already open at that stop, the crossing is recorded on it —
`cost_exposure_paise` recomputed, a line appended to `audit` — and no new
exception is opened. The gate being shut is what the driver is dealing with;
the free time running out is a property of that. One problem, one card on the
board. A crossing with nothing else open at that stop opens its own.

**Wording is part of the spec.** These produce *"everything alright? need
anything?"* — never *"driver unresponsive"*, never a count of how often it
fired. See CLAUDE.md rule 1.

### 4.4 Exception closure

| Open | Resolved by |
|---|---|
| `GATE_CLOSED` | `SERVICE_STARTED`, `STOP_COMPLETED` |
| `CONSIGNEE_ABSENT` | `STOP_COMPLETED`, `REATTEMPT_SCHEDULED` |
| `STOP_OVERDUE` | `ARRIVED_STOP` |
| `DRIVER_SILENT` | any driver-sourced event |
| `WINDOW_AT_RISK` | `STOP_COMPLETED` before close |
| `VEHICLE_BREAKDOWN` | `DEPARTED` |
| `DETENTION_CROSSED` | `SERVICE_STARTED`, `DEPARTED` |

Still open at trip close → `EXPIRED`.

---

## 5. The safety critic

Four signals, combined in code. The model's self-rating is one capped input.

```python
signals = {
    "entity_resolution":     1.0 if not event.unresolved_fields else 0.0,
    "transcript_legible":    stt_confidence_normalised,
    "retrieval_score":       top_sop_similarity_normalised,
    "self_rating":           model_confidence,        # weight capped at 0.20
}
confidence = weighted_sum(signals)

risk = HIGH if (
    reply_states_policy_affecting_driver_pay
    or cost_exposure > threshold
    or action_type == "DRAFT_CUSTOMER_MESSAGE"
    or intent == Intent.CORRECTION
) else LOW
```

`retrieval_score` is the **top cited SOP chunk's** similarity, and 0.0 when
nothing was cited. Precedents do not count toward it, however well they match.

**"No SOP cited" means no chunk cleared the relevance floor — not that no chunk
came back.** The distinction is the whole of rule 7. A vector store returns its
top-k for every query, so a chunk always comes back; if that counted as a
citation the `ESCALATE` branch below would be unreachable and Sarathi would
answer a question about a burst tyre out of the delivery-window section.

The floor is measured, not chosen. Cosine similarity has no absolute meaning
across embedding models: the seeded corpus scores ~0.60 against questions it
has no answer to and ~0.65-0.75 against real ones, so any hand-picked threshold
is both arbitrary now and silently wrong the next time the model changes. So at
index time each customer's corpus is queried with two or three deliberately
off-topic probes, the highest score any of them reaches is stored as that
corpus's noise level, and a chunk counts as cited only if it clears
`baseline + CITATION_MARGIN`. Re-measured on every index, so a model change
re-derives the floor instead of invalidating it unnoticed.

The baselines are per customer and they differ — 0.608, 0.635, 0.621 on the
seeded three under gemini-embedding-001 — which is the argument against a
single constant in one line.

The floor filters obvious noise. It is **not** where rule 7 is enforced, and
§5.1 is why — read it before designing anything against `retrieval_score`.

Rule 7 governs claims about what the driver is owed or is liable for, and the
only thing that settles those is the customer's standing terms. A precedent is
a resemblance — *this looked like that, and that was resolved this way.* It is
useful context for a decision. It is not a source of authority about terms.
Letting a strong precedent stand in for a missing SOP would have Sarathi citing
its own past behaviour as the reason a driver is covered, which is the
mechanism by which one mistake becomes policy — and §6 already keeps unreviewed
decisions out of the index for the same reason.

So a well-precedented situation with thin SOP coverage escalates. **That is the
intended behaviour, not a gap in it.** It is exactly the case where Sarathi says
it will find out and routes to a human, which is the honest answer when nobody
ever wrote the terms down.

One implementation, `RetrievalResult.retrieval_score`. A signal that means
different things in different callers is not a signal.

Router:

| Condition | Mode |
|---|---|
| `confidence >= 0.75` and `risk == LOW` and SOP cited | `SPEAK` |
| `0.5 <= confidence < 0.75`, or `risk == HIGH` with SOP cited | `SPEAK_HEDGED` |
| `confidence < 0.5`, or no SOP cited, or entity unresolved | `ESCALATE` |

Weights, summing to one: `entity_resolution` 0.30, `transcript_legible` 0.25,
`retrieval_score` 0.25, `self_rating` 0.20 — at its cap, because it is the one
signal that can be confidently wrong for the same reason the reply is.

**"No SOP cited" attaches to claims, not to replies.** A reply that asserts
nothing about the customer's rules needs no citation: *"gate closed at stop 2,
waiting counted from 10:12"* restates our own records, and requiring a SOP
behind it would escalate every acknowledgement on the shift. Rule 7 is worded
the same way — no claim about pay or liability *without* a cited SOP. No claim,
nothing to cite. So the ESCALATE row fires when a claim was made and did not
survive grounding, not when `claims` is simply empty.

Two hard overrides regardless of score:

1. Any claim about **what the driver is owed or liable for** requires a cited
   SOP that supports it. No citation, no claim — Sarathi says it will find out.
   A claim that fails grounding is not merely dropped from `cited_sop_ids`:
   the drafted text still contains the sentence that made it, so the draft is
   not spoken at all. The reply is rebuilt from `restated_facts`, which came
   from state and were never in question.
2. Any outbound customer communication requires human approval.

`ESCALATE` still speaks to the driver. It says a person is looking at it. It
never returns silence.

### 5.1 Similarity ordering is fragile, so grounding enforces rule 7

#### What was measured — 10 September 2026

Held-out off-topic probes against the seeded corpus under
gemini-embedding-001. Held out deliberately: not among the three the baseline
was measured from, so this is what the calibration does on inputs it has not
seen. Against customer-1:

```
0.640   "diesel price at the pump near Aluva"              off-topic, held out
0.635   "nobody is answering, who does the driver call"    a real question
```

The noise scored higher than the question. Not a narrow margin — **inverted
ordering**, which no cut point survives: any floor admitting the real question
admitted the diesel one, and any floor excluding the diesel one stranded a
driver at a locked shop asking who to ring.

#### What changed — 11 September 2026

The cause was **vocabulary drift between the driver's words and the document's
headings.** He asks about *waiting*; the section is headed *Detention*. He is
at a shut gate asking one question, and the answer was split across a gate
section and a detention section that did not reference each other. Embedding
similarity was being asked to bridge a gap that belonged in the document.

Two changes to the SOPs, no change to the retrieval code:

1. A plain-language line under every section heading, in the words a driver
   would use — under *Detention*, *"how long can I wait before it starts
   costing, who pays for waiting, when does the clock start."* This is the
   query side of the gap written into the document. It is what these chunks are
   retrieved by.
2. Customer-2's gate section now says what happens to waiting time when the
   gate is shut, and points at the detention terms, so one question gets one
   chunk.

Remeasured, same pair, same probes, same model:

```
              10 Sep    11 Sep
noise         0.640     0.630
question      0.635     0.649
```

The ordering held on all three customers: every real question now outranks
every held-out probe. Baselines *fell* — 0.608 to 0.591 on customer-1, 0.621 to
0.611 on customer-3 — because the documents became more distinguishable from
noise, which is what those lines are for. Headroom roughly doubled: +0.026 to
+0.058, +0.037 to +0.039, +0.029 to +0.052.

The seeded gate-closed case went from `ESCALATE` with a rejected claim and two
undeclared ones, to `SPEAK_HEDGED` with three grounded claims.

#### What changed again — 11 September 2026, once the query builder was read

Everything above was measured against a query the pipeline never built.

`build_query` took a `stop_context` mapping and embedded it whole. The
calibration passed it `{"situation": probe}` and nothing else. Production
passed the entire resolved stop — `stop_id`, `customer_id`, `seq`,
`service_minutes`, the stop status, and five ISO-8601 timestamps — and then the
driver's words. **So the floor was measured on one query shape and applied to
another**, which makes every number in the two sections above a measurement of
something that was not running.

What production actually scored, against the floor production actually used:

```
                                              query as built   floor    result
customer-2  "am I going to get paid for this waiting"   0.635   0.655   no citation
customer-2  "how much free waiting time do I have here" 0.630   0.655   no citation
```

Calibrate both sides on that same shape and it is worse, not better — the
pollution is common to the probe and the question, so it lifts the floor with
the signal:

```
                  baseline   headroom   real questions under floor
customer-1           0.600     +0.030   0 of 6
customer-2           0.644     +0.003   4 of 6
customer-3           0.615     -0.001   3 of 6
```

Customer-3's worst real question scored **below that customer's measured
noise**. Not near it. Below it.

**The fix is a deletion.** The query is the driver's words — his transcript, and
the interpreter's plain-English rendering of his question where he asked one —
and nothing else. `customer_id` still selects the corpus in SQL and is no longer
embedded: an identifier is not a thing a driver said.

```
              11 Sep (a)   11 Sep (b)
              JSON + stop  words only
customer-1       0.600        0.574     baseline
customer-2       0.644        0.563
customer-3       0.615        0.595
headroom         +0.003       +0.191    customer-2, worst question over noise
```

Every real question clears the floor on all three customers. No held-out probe
does, on any of them.

The reason it was this expensive is the part worth keeping, because it
generalises past this bug:

> **Anything added to every query is in the noise probe too.** Common text
> raises the measured baseline exactly as fast as it raises a real question,
> while pulling every query toward the same point and compressing the distance
> between them. A floor is `baseline + margin`, so boilerplate spends headroom
> and buys no discrimination.

Measured, that is why nothing else was kept. Adding the event type back — one
short English phrase, the most defensible thing on the list — costs about 0.04
of floor and drops a real romanised-Malayalam gate report under customer-3's:
0.638 against a floor of 0.655. A plain-language stop descriptor costs more.
Neither is in the query.

#### Two things this dissolved, neither of them by being tuned

**The goodwill probe was never the problem.** Section 5.1 recorded customer-2's
floor as held about 0.03 too high by one probe — *"quarterly amortisation of
goodwill in the consolidated accounts"* at 0.635, corporate-accounting
vocabulary landing close to a warehousing document — and left it as a known
limitation rather than swap it out. It is now the **lowest** of that customer's
three probes, at 0.540, and the lowest on the other two customers as well:

```
                          10-11 Sep   now
the front tyre has burst     0.606    0.544
where can I get lunch        0.598    0.563   <- holds customer-2's floor now
quarterly amortisation       0.635    0.540
```

The JSON envelope was what made it score. Wrapping every probe in
`{"customer_id": ..., "event_type": ..., "stop_context": {"planned_arrival":
"2026-09-10T10:30:00+05:30", ...}}` lends an accounting probe the exact
register it needs to resemble a business document. The probe set was not
touched to achieve this and must not be touched now — the discipline of not
fitting the calibration to its own test is what left the anomaly visible long
enough to be explained instead of hidden.

**The margin window closes.** It was empty by a thousandth: customer-1 needed
`>= 0.040` to exclude its held-out diesel probe, customer-2 needed `< 0.039` to
admit its worst real question. Remeasured on the corrected query:

```
customer-1   margin must be > +0.014  and < +0.174
customer-2   margin must be > +0.015  and < +0.191
customer-3   margin must be > -0.010  and < +0.148

window       (+0.015, +0.148)        CITATION_MARGIN = 0.02, inside it
```

One value now separates all three customers, which no value could before. That
is a repaired input, not a better threshold, and it changes nothing about where
rule 7 is enforced.

Worth recording against the next change: 0.02 sits 0.005 above the bottom of
that window and 0.128 below the top. The bottom is the edge where noise gets
cited and the top is the edge where real questions escalate, and SPEC 5 prefers
the second. The margin is nearer the wrong edge than it looks.

#### The finding that survives

Fixing the documents fixed the ordering. It did not make the ordering
trustworthy.

> **Similarity ordering is fragile under vocabulary drift, so it cannot be what
> enforces rule 7.** A wording change in a document — not in the code, not in
> the model, not in the threshold — moved noise above a real question and back
> again. Anything that can be inverted by an editor choosing the word
> *Detention* over the word *waiting* is not a safety mechanism.

**This conclusion does not depend on the ordering being inverted today. It
depends on it having inverted at all.** The corpus is three short documents
written in one sitting; a real account's SOPs are written by different people
over years, and nothing keeps their headings in the driver's vocabulary. The
next drift will not announce itself with a failing test.

So the floor stays and keeps its job: it filters obvious noise, it makes the
`no SOP cited` branch reachable at all, and a chunk above it is a chunk worth
*looking* at. It is not evidence that the chunk supports the claim.

#### Where rule 7 is enforced: the grounding check

After the responder drafts and before the router speaks, the critic asks a
separate question of the cited chunk text — *does this passage actually support
this claim?* — for each claim about pay, liability, or what the customer's
terms require. Not similarity. Entailment, against the text that would be
cited.

- **It catches what the floor cannot.** A question about diesel retrieves a
  delivery-window chunk, and nothing in that chunk mentions fuel. Similarity
  says 0.630; grounding says no. Grounding's answer does not move when someone
  rewrites a heading.
- **It is cheap.** One short call over a drafted reply and at most three
  chunks. No history, no trip state.
- **It fails safe.** Unsupported, unparseable, or the call did not complete: no
  citation, therefore no claim, therefore `ESCALATE` — which still speaks, and
  still hands the driver his facts back.
- **It is a second signal, not a better threshold.** Retrieval says *this text
  is nearby*; grounding says *this text says that*. Both being wrong takes two
  independent failures, and §2's restatement loop sits under both.

Built at M3. The shape it needs is in §3.4: claims small enough to check one at
a time, each carrying the chunk it rests on, and `cited_sop_ids` derived from
them rather than listed beside them.

#### The margin, and why it was left alone long enough to be fixed properly

Until 11 September `CITATION_MARGIN` could not be made to work. No single value
separated the three customers: customer-1 needed `>= 0.040` to exclude its
held-out diesel probe, customer-2 needed `< 0.039` to admit its worst real
question, and the window was empty by a thousandth. The blame went to
customer-2's baseline, held about 0.03 high by the goodwill probe, and the
entry closed by refusing to replace that probe — a baseline that is too high
fails safe, and swapping out a probe because it scored high is fitting the
calibration to its own test.

That refusal was the right call for the wrong reason, and it is the reason this
was fixable. **Neither the probe nor the margin was the defect.** The query
builder was putting ISO timestamps and record ids into the vector, the probes
were getting the same envelope as the questions, and the envelope was what the
goodwill probe was scoring against. Tuning either number would have buried that
under a threshold that appeared to work, and the next model change would have
unburied it with no way to tell what had moved.

Both numbers now stand on their own, and neither was touched to make it happen.
The figures are in *Two things this dissolved*, above.

The rule that produced this outcome is worth keeping in exactly the form it was
first written: **do not fit the calibration to its own test.** Applied twice —
holding probes out, then declining to swap one when it scored badly — it turned
a tuning problem into a defect report. A probe that scores strangely is
evidence about the system, and a system that is being tuned cannot produce
evidence about itself.

---

## 6. Retrieval

- **`sop_chunks`** — per-customer standing instructions. Detention terms, free
  time, reattempt rules, gate procedure, site contacts, delivery windows.
- **`precedents`** — resolved exceptions and their outcome, written **only**
  when a human has marked the resolution approved. An unreviewed decision must
  never become the justification for the next one.

**The retrieval query is the driver's words and nothing else** — his
transcript, plus the interpreter's plain-English rendering of his question
where he asked one. No ids, no timestamps, no stop fields, no event type, no
intent. `customer_id` selects the corpus in SQL and is not embedded.

That is narrower than it reads, and deliberately so: anything added to every
query is also in every noise probe, so it raises the measured floor as fast as
it raises a real question. §5.1 has what the previous version cost. Top-3 each.

---

## 7. Surfaces

**Driver (primary).** A phone-shaped page: hold-to-record, a transcript of what
was heard, Sarathi's spoken reply with the text beside it, and a plain list of
what has been recorded about today so far. That last panel is the dignity
feature — he can see his own record.

**Dispatcher (secondary).** Three columns — NEEDS ATTENTION / HANDLED / RUNNING
FINE — plus the approval queue and a clock slider replaying the shift.

No fleet map. No driver leaderboard. No per-driver history view. Their absence
is a design position, stated in the README.

---

## 8. Scope

**v1, must finish:** 1 trip · 5 stops · text input · interpreter · identity
resolver · state machine · watchdog · exception lifecycle · SOP retrieval ·
responder · safety critic · router · driver page · dispatcher board · traces.

**v2:** voice in and out · driver questions as a separate path · corrections ·
precedent write-back on approval · LangGraph port · FastMCP tools.

**v3 or never:** multi-vehicle · real WhatsApp · real TMS · route optimization ·
auth · anything resembling driver analytics.

Ship v1 publicly before starting v2. Solo projects die at 60% complete, and the
cure is having shipped something at 40%.
