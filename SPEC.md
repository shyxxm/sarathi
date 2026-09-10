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
class DriverReply:
    mode: ReplyMode
    language: Language
    text: str                       # in the driver's language
    restated_facts: list[str]        # what we understood — always populated
    cited_sop_ids: list[str]
    audio_path: str | None = None
```

`restated_facts` is not optional and not decorative. It is §2.

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

Router:

| Condition | Mode |
|---|---|
| `confidence >= 0.75` and `risk == LOW` and SOP cited | `SPEAK` |
| `0.5 <= confidence < 0.75`, or `risk == HIGH` with SOP cited | `SPEAK_HEDGED` |
| `confidence < 0.5`, or no SOP cited, or entity unresolved | `ESCALATE` |

Two hard overrides regardless of score:

1. Any claim about **what the driver is owed or liable for** requires a cited
   SOP. No citation, no claim — Sarathi says it will find out.
2. Any outbound customer communication requires human approval.

`ESCALATE` still speaks to the driver. It says a person is looking at it. It
never returns silence.

---

## 6. Retrieval

- **`sop_chunks`** — per-customer standing instructions. Detention terms, free
  time, reattempt rules, gate procedure, site contacts, delivery windows.
- **`precedents`** — resolved exceptions and their outcome, written **only**
  when a human has marked the resolution approved. An unreviewed decision must
  never become the justification for the next one.

Retrieval query is built from `intent + event_type + customer_id + stop
context`. Top-3 each.

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
