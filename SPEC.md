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

### 1.1 Where the model boundary goes

**A decision that looks like language work but is really a question about
state belongs to code, and the model must not be the one answering it.** The
test: *could the answer be wrong in a way that only our state would reveal?* If
so, code answers it, and the model's contract has no field to put an answer in.

Three times now:

- **Identity (M2).** "rand mathe godown" reads like a place to interpret. Which
  stop it is depends on where the trip is. `InterpreterOutput` has no `stop_id`,
  so a model that guesses one fails validation (rule 3).
- **The language he is spoken to in.** One romanised transcript looked like
  Hindi, and a Malayalam speaker was answered in three scripts. The language he
  speaks is a fact in his record; code passes it in and ignores the draft's own
  label.
- **Which figures are ours (§5.2).** Whether "13 minutes of free time left" is
  a record or a claim about the customer's terms reads like a judgment about a
  sentence. It is a question about where the figure came from, which only code
  knows. The responder has no `restated_facts` field; code writes them.

`mode` was built this way from the start (§5): a prompt that can name its own
routing is a prompt deciding when to escalate.

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

**Transcription confidence cannot establish correct interpretation.** These
experiments supplied text, not audio: the four planned voice notes had not
been recorded. m07's words arrived intact and were then overridden. A future
recogniser could correctly transcribe them and the interpreter could still
get them wrong. No confidence value was measured in those experiments.

**First observed instance outside the constructed test — 12 September 2026,
real voice note 02.** Sarvam returned:

```
ഇവിടെ ആരുമില്ല phone എടുക്കുന്നില്ല shop കൂട്ടിയ പോലെയുണ്ട് ഇപ്പോൾ എന്ത് ചെയ്യും
```

The speaker confirms that `shop കൂട്ടിയ പോലെയുണ്ട്` is garbled; the supplied
meaning is "the shop looks closed." The verbatim spoken line is pending and
must be added before recording the exact word-level error. Haiku still returned
`CONSIGNEE_ABSENT`, with `REPORT` and `QUESTION`, and marked the transcript
legible. **The classification was correct despite a wrong transcript, not
because the transcript was right.** This is the first observed real-audio
instance of the §2.1 frame-completion pattern: the surrounding absence, phone
and request-for-help frame survived the damaged shop phrase and yielded the
expected event. Unlike m10gg, the absence negation itself survived here; this
case does not demonstrate a real-audio negation inversion.

That correct event must not be counted as evidence that the interpreter checks
transcription fidelity. It is the strongest evidence so far from real audio
for placing the safety burden on §2's restatement loop, not on the interpreter:
the driver must hear what was recorded and have the chance to correct it.
Sarvam supplied no transcription confidence to flag the damage. This run did
not test a driver correction or establish that the loop guarantees safety.

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
- **The damaged-transcript subclass has no confidence mitigation today.**
  Sarvam Saaras v3 does not expose transcription or segment confidence.
  `language_probability` measures the detected language, not whether it heard
  the right Malayalam, and is never substituted. We cannot claim the recogniser
  was unsure about `illa`, or that its score catches a missing negation. Missing
  confidence earns zero credit in §5. That makes voice replies more cautious;
  it does not detect the error. **The restatement loop is the only guard for
  voice transcription and interpretation errors today.** If another provider
  supplies real segment confidence, use the minimum and measure whether it
  tracks error before treating it as a mitigation.

**Unresolved — 11 September 2026: two unrelated changes moved m07, in
opposite directions.** One paragraph was added to the interpreter prompt for an
unrelated failure (§2.2): every message is his words to classify, never a
question put to you. Nothing in it is about gates, negation or frames. m07 went
from `GATE_CLOSED` to the correct `SERVICE_STARTED` — 3 of 3 on Haiku at
temperature 0, against 3 of 3 `GATE_CLOSED` on the previous prompt. Then the
interpreter's reply was prefilled with `{`, also for §2.2 — tested and declined
there — and m07 went back to `GATE_CLOSED`, 3 of 3. m10gg moved under neither.
The shipped prompt has no prefill, so m07 currently reads correctly, for no
reason anyone can name.

This is not a fix, and m07 is not promoted out of the boundary set. Nothing
explains why either change reaches this row. It is evidence for how fragile the
boundary is: the reading turns on prompt text and call mechanics that have
nothing to do with it, and it flips without announcing itself. Not investigated
further; §2's restatement loop remains the mitigation.

This is the single most important section in the file. A pipeline that assumes
good transcripts will feel broken to a real driver in the first minute.

### 2.2 When the failure is ours

**Every message he sends ends up somewhere a person can see it: on his trip as
an event, or on the dispatcher board as a processing failure. Never neither.**

On 11 September 2026, *"How much free waiting time does this customer
allow?"* — clean English, an unambiguous question — made the interpreter
answer in prose instead of returning its JSON. It took the question as put to
itself. The service caught the parse error and told him *"I couldn't read your
message just now. It has not been recorded. Please try again."* The first
sentence was false: his words were perfectly readable. The last was useless:
at temperature 0 the failure is deterministic, so he retries, it fails again,
and his question reaches nobody.

- **Illegibility and failure never share a message.** Illegible is a statement
  about his words — `transcript_legible` false (§3.2) — and it asks him to say
  it again. A failure is a statement about us. Telling a driver his words were
  unreadable when the system broke is a false statement about him.
- **A failure is escalated, not retried.** Any interpreter failure — prose
  instead of JSON, a shape the contract refuses, a provider still down after
  its retries — puts his raw text on the dispatcher board as a *processing
  failure*. It is not an operational exception: no event, no stop, no state
  transition, nothing on his trip record. It is a message a person answers.
- **He is told exactly that.** The fault is ours, not his words; nothing was
  recorded on his trip; a dispatcher has his message. `restated_facts` still
  holds (§3.4). There is no reading of his to restate, so it restates what
  happened to his message.
- **Dropping his words is worse than recording that we failed to understand
  them.** So a message we could not read still produces a record. An error
  string with nothing kept is silence with extra steps: he believes he asked,
  nobody has it, and the one thing a person could act on — what he said — has
  been thrown away.

The failure record is not a trip event, so the watchdog (§4.3) still measures
silence from his last *recorded* message and may check in on him after a
failure. That is the cheap direction to be wrong in.

Calibration counts this as a wrong answer. `scripts/check_interpreter.py` keeps
a row out of the score only when the provider did not answer or could not be
asked; a model answering in prose once sat in the same bucket as a 429, and
this whole failure class never reached the score.

**Tested, measured and declined: prefilling the reply with `{`.** It looks
like the obvious fix for a model that answers in prose, and for m06q4 it is
one. It is not shipped, and this is why.

A prompt rule — every message is his words to classify, never a question put
to you — did not stop the prose. Opening the model's reply with `{` did,
because Anthropic continues an assistant turn it is handed and prose has
nowhere to start. (Ollama restarts the object instead of continuing it, so the
brace would only ever have gone to Anthropic.) Haiku 4.5, temperature 0,
11 September 2026:

```
                                                   with prefill       without
m06q4  How much free waiting time does this        QUESTION     3/3   prose, not JSON   3/3
       customer allow?
m08    over                                        CHITCHAT     3/3   REPORT / UNCLEAR  3/3
```

m08 is the case the calibration set was built around: a legible message with
no event in it, which has to get *over what?* As `CHITCHAT` it records an
acknowledgement and he hears *your message was received* — an ambiguous message
quietly accepted, the silent guessing §2 exists to prevent. Without the
prefill, m06q4 fails loudly and honestly: he is told the fault is ours and a
dispatcher gets his words. **A loud, honest failure that reaches a person beats
a quiet acknowledgement of an ambiguous message.** So m06q4 stays a failing
held-out row, handled by the escalation above, until something fixes it
without costing m08.

The prefill also moved m07 back to `GATE_CLOSED`, 3 of 3 (§2.1).

### 2.3 A second stated boundary: verified is not the same as said

**A claim can be grounded, accepted, and then not appear in the reply.**
Nothing checks that what was verified is what was spoken.

Found on 11 September 2026, trying the responder on the cheap tier (Haiku 4.5)
to cut latency. At a shut gate (m06) it returned two claims, and the grounding
check, on Sonnet, accepted both against customer-2's terms:

```
[...-003]  A closed gate does not stop the waiting clock — waiting for the      supported
           entrance to open counts from recorded arrival time.
[...-002]  Free waiting time is 60 minutes from arrival; beyond that,           supported
           150 paise per minute.
```

The spoken text carried the second and not the first. The one sentence a driver
at a shut gate most needs — his waiting is being counted even though the gate is
shut — was verified, accepted, listed on the driver page as a citation, and never
said. The router spoke the reply as `SPEAK_HEDGED` at 0.91.

Grounding cannot see this and was never built to. It checks what is *claimed*:
each claim against the passage it cites, and the prose for assertions no claim
declares (`unclaimed_assertions`). An omission is neither. A reply that says
less than it verified passes every check there is, and the error runs the bad
way: what drops out is the rule that protects him.

This is the same class as §2.1: the checks score the reply as trustworthy
because, by every signal they have, it is. The composite even rose — Haiku rated
its own reply 0.92, against Sonnet's 0.82 on the fuller one.

The mitigation for now is a model choice, not a check. The responder stays on
the strong tier, which spoke the rule in the same case. That is one sample, not
a guarantee. A check would have to establish that each grounded claim's content
is present in `text` — entailment across languages, since claims can come back
in English under Malayalam prose — so it is a model call, and it would sit
beside grounding. **Not built.**

**A second case, the other way round.** Once code wrote the facts (§5.2), the
consignee-absent reply's record said `Delivery window 11:30–13:00.` The
responder spoke it as "ഡെലിവറി വിൻഡോ 11:30 മുതൽ 1:00 വരെ" — 13:00 as 1:00,
under a prompt that says to keep every number exactly as the list has it. The
first case was a claim verified and never said. This one is a fact on record,
said differently. They are the same gap: nothing checks what is spoken against
what is on record — not the claims grounding accepted, and not the facts code
wrote.

The only guard today is §2's restatement loop: he hears the figure and can
correct it. It holds here because what he heard is still a correct time — one
in the afternoon is 13:00. It would not hold for a figure he has nothing to
check against. A rate or a free-time allowance rendered loosely — 150 paise a
minute heard as 15, sixty minutes free heard as sixteen — is not something a
driver catches by hearing it, because he never had the right number to begin
with.

### 2.4 STT ambiguity collapse

**The recogniser can resolve an ambiguity that belongs to the driver.** This
is a separate failure class, upstream of §2.1's interpreter frame-completion.
STT emits a clean-looking choice; the interpreter receives that choice without
the alternatives or the uncertainty in the original speech. Nothing downstream
can recover the fact that a choice was made from the transcript alone.

**Finding — real voice note 04, 12 September 2026:** the supplied reference
was "Over", explicitly described by the speaker as unclear about what was
over. The two paths produced:

| Input path | Text seen by interpreter | Interpreter result |
|---|---|---|
| Typed reference | `Over.` | `UNCLEAR`, `REPORT`, legible=true; `event_type` unresolved |
| Recorded speech through Sarvam | `കഴിഞ്ഞു.` (finished/over) | `STOP_COMPLETED`, `REPORT`, legible=true; no unresolved fields |

The speaker's diagnosis is **STT ambiguity collapse**: spoken "over" becomes
`കഴിഞ്ഞു`, a word meaning finished, and the ambiguity handled correctly on
the typed path is no longer represented as uncertainty on the voice path.
The resulting event looks like an ordinary completion report.

**Evidence boundary:** the exact spoken line has not yet been independently
verified against a verbatim reference. The supplied English reference and
Malayalam transcript differ in language, and `കഴിഞ്ഞു` itself still does not
name what finished. These outputs establish the unsafe difference between the
two paths; they do not by themselves isolate how much of the choice was made
by the recogniser versus the interpreter's treatment of Malayalam. Preserve
that distinction when adding the verbatim evidence. No prompt was changed or
transcript repaired for this comparison.

**STT ambiguity collapse is invisible to the composite score, to grounding,
and to `unresolved_fields`.** The composite scores the signals it receives;
legible text does not disclose a discarded alternative. Sarvam supplied no
transcription confidence. Giving that unavailable signal zero credit (§5)
makes voice more cautious but does not identify this particular collapse.
Grounding checks claims against sources, not the recogniser's choice against
the original speech. `unresolved_fields` describes what the interpreter still
needs; here it was empty. None of these checks preserves the driver's original
ambiguity or establishes that he meant delivery completion.

**The restatement loop is again the only guard today.** He hears *"delivery
completed, stop 2"* and can say *no*. The stop identity comes from code, as
always; the point is to expose the concrete event we recorded. A courteous
acknowledgement would hide that decision. Read-back now carries the safety
burden for three distinct failure classes: wrong words from transcription,
wrong comprehension through interpreter frame-completion (§2.1), and ambiguity
collapsed by STT before interpretation. This is further evidence that
`restated_facts` is load-bearing. It is not evidence that a driver correction
was exercised successfully: this evaluation ran STT and interpretation only,
without applying a trip event or testing the correction loop.

One call took 0.668 s for STT and 1.592 s for interpretation. Transcription
confidence was unavailable; language probability 0.946 is not a substitute.
The six-note set has five expected intent/event readings and this failed
ambiguity case, not six successes. Raw evidence is in
`recordings/stt-results/2026-09-12-note04/`.

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
class ResponderOutput:               # the model boundary. no mode, no facts.
    language: Language
    text: str
    claims: list[Claim]
    confidence: float                # §5 caps its weight at 0.20

@dataclass
class DriverReply:                   # what the router assembles
    mode: ReplyMode
    language: Language
    text: str                       # in the driver's language
    restated_facts: list[str]        # written by code from state — always populated
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
the clock — is written by code, not the responder (§5.2), and is checked by the
driver himself when he hears it back (§2).
`claims` comes from retrieval, asserts what the customer's rules say, and is
checked against the chunk text before it may be spoken. Facts are never
grounded against a SOP and claims are never taken on trust.

**The spoken reply is selective; the facts list is complete.** M6 made the
cost of reciting every fact audible: m06 took 36 seconds. Target ten to fifteen
seconds, including the router's follow-up. Lead with what he needs most — at a
shut gate, whether his waiting still counts under the cited rule. One idea per
short sentence. Let the on-screen list carry the routine recap. The reported
problem must still be recognisable in the answer, and anything he told us that
we recorded differently must still be read back (§2). When waits differ, say
his claimed minutes, our counted minutes and when our count starts. Never cut
that comparison or the relevant protection rule to meet a duration target.

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

### 3.7 Parsing model output

**Every parser tolerates surrounding prose.** Models say more than they are
asked to: a fence around the object, a sentence before it, a note after it.
Three times on 11 September 2026 that broke a parser here, and each time the
error surfaced as something else:

- The interpreter answered a driver's question in prose instead of returning
  its JSON (m06q4). The driver was told his message could not be read (§2.2).
- The same failure, in calibration, was filed with 429s as a provider error,
  not a wrong answer, and the whole failure class stayed out of the score.
- Grounding returned correct verdicts in a fence, then a note after them. The
  check counted as not having run; the replay capture reported it as "check
  the configured models" (§5.2).

So the interpreter, the responder and grounding all read through one extractor,
`agents/model_json.py`: the single fenced block if there is one, otherwise the
single JSON object in the text. Prose around it is not an answer, and it is
ignored.

Still refused: no object at all — a model that answered in prose has failed —
and more than one, because choosing between them would be a guess. And the
other half of the rule: **a parser's failure is reported as a parser's
failure**, naming the node and quoting the output. Never as a statement about
the driver's words, the provider, or the configuration.

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

**And the read-back has to split it back.** Until 11 September 2026 the
read-back said "You set off — stop 1" for both "left the depot, heading to stop
1" and "left stop 1". A driver hearing it could not tell which we had recorded,
so he could not correct it — the restatement loop (§2) broken on the most
frequent event of the shift. It now says which, in two lines code chooses: *You
set off for stop 1* and *You left stop 1*. The choice is read from the log —
`left_a_stop`: an earlier event finished that stop — not from the stop's status,
because his record is read back hours later, when every stop has moved on.
Collapsing a type the model cannot tell apart is right; the words he hears must
still carry what the type does not.

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

**Voice confidence is a measurement, not a proxy.** The second signal uses
the provider's transcription confidence, taking the minimum across segments
when supplied. One badly heard word matters more than the average. A missing
segment score makes that evidence unavailable. Never substitute language
detection probability or the interpreter's own confidence.

When a voice provider supplies no transcription confidence, keep the measured
value `null`, show **unavailable**, and give this signal **0.0 credit**. Do not
renormalise the weights. This deliberately lowers the composite by 0.25 versus
the equivalent legible typed input and can cause more hedging or escalation.
The zero is a scoring policy, not a claimed measurement of zero accuracy.
Typed input retains its existing legibility score, and an interpreter that
finds a transcript illegible still gives this signal zero regardless of STT.
Routine reports that only receive a code-written read-back still bypass the
critic, as before; voice does not introduce a new downstream pipeline.

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

window       (+0.015, +0.148)        one value now fits all three
```

One value now separates all three customers, which no value could before. That
is a repaired input, not a better threshold, and it changes nothing about where
rule 7 is enforced.

#### Where in that window, and what the choice costs

`CITATION_MARGIN` is **0.05**. It was 0.02 until the window was measured, which
put it 0.005 above the bottom and 0.128 below the top.

**The two edges are not symmetric, so the middle of the range is the wrong
place to sit.** Below the window a chunk that merely shares the driver's
vocabulary gets cited and Sarathi tells him what his customer's terms are on
that basis. Above it, a real question finds nothing, and he is told a person
will find out — to someone who can actually answer him. SPEC 5 says which of
those to prefer, in as many words: *a floor that is too high fails safe.* So
the margin is set well clear of the bottom and leaves the slack at the top,
where slack is cheap.

At 0.05, remeasured on all three:

```
              baseline   floor   worst held-out   clearance   worst question   over floor
customer-1      0.574    0.624       0.588          +0.036        0.748          +0.124
customer-2      0.563    0.613       0.578          +0.035        0.753          +0.140
customer-3      0.595    0.645       0.584          +0.061        0.743          +0.098
```

Every held-out probe excluded on every customer, every seeded question admitted
on every customer, by 0.098 at the tightest.

**What it costs is one real message, and it is the right one to lose.** The
damaged transcript from §2 — `ivide aar illa pon edukkunil chaap pootti pol und
ipp enthu cheyy`, the row that reads as *damaged, meaning intact* — scores
0.594 to 0.607 and now clears no customer's floor, where at 0.02 it cleared two
of three. Its clean counterpart scores 0.651 to 0.659 and still cites
everywhere.

```
                                          customer-1  customer-2  customer-3
clean     ivide aarum illa phone edu...      0.659       0.655       0.651
damaged   ivide aar illa pon edukkunil...    0.607       0.594       0.599
```

Damage costs about 0.05 of similarity, which is exactly the gap this margin now
spans. That is not a coincidence to design around — it is the honest shape of
the problem. **A transcript we only half heard is a worse query, and a worse
query is a worse reason to state someone's contractual terms back to him.**

This does not contradict §2. §2 says a damaged transcript must still be *read*,
and it still is: the interpreter reports the event, the state machine records
it, and the driver gets his own facts back. What he does not get is a claim
about what he is owed, sourced from a chunk retrieved on half a sentence. He is
told Sarathi will find out. That is §2's mechanism working — the reply hedges
rather than guessing — not an exception to it.

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
- **It checks what is claimed, not what is spoken.** A grounded claim that
  never reaches `text` passes. That gap is stated in §2.3 and not closed.

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

Both numbers now stand on their own. The figures are in *Two things this
dissolved*, above.

The margin was raised afterwards, from 0.02 to 0.05, and that is a different
act from the one being warned against here. It was not moved to rescue a case
that was failing; it was moved because a window that had never been measurable
before could finally be measured, and 0.02 turned out to sit at the wrong end
of it. Tuning to make a test pass is fitting the calibration to its own test.
Reading a distribution and placing a threshold in it on a stated safety
preference is what the distribution is for. The test to apply is whether you
would have to change the number back if the failing case were removed — here
you would not.

The rule that produced this outcome is worth keeping in exactly the form it was
first written: **do not fit the calibration to its own test.** Applied twice —
holding probes out, then declining to swap one when it scored badly — it turned
a tuning problem into a defect report. A probe that scores strangely is
evidence about the system, and a system that is being tuned cannot produce
evidence about itself.

### 5.2 Two buckets: what we computed, and what their terms say

**A fact derived from our own state needs no citation; it is checked by the
driver hearing it back (§2). A claim about the customer's terms needs a
citation; it is checked by grounding. A figure lands in exactly one of those
buckets, and the responder must not be free to choose wrongly.**

Our state: the clock `domain/detention.py` computes — when counting started,
minutes waited, free minutes, minutes left, billable minutes, exposure — the
stop and its window, the time, and what he told us. Free minutes and the rate
are customer terms, but ones we hold as structured data and compute from; as
figures, they are our state. Their terms, as claims: what the standing
instructions *say* — which counter to report to, whether a shut gate stops the
clock, who bears a failed delivery.

The consignee-absent case broke this on 11 September 2026. The responder did
the right thing: "free time 15 minutes, 13 left" went in `restated_facts`, and
its one claim was the delivery-counter rule, cited and grounded. Its prose said
the figure again in other words — "it is 12:02, the window runs to 13:00, 13
minutes of free time remain". Grounding, told to ignore anything under *What we
already knew* "including where the reply words them differently", flagged the
sentence anyway: "a rule-derived calculation … which bears on when detention pay
would begin". An unclaimed assertion is a hard override (§5), so a correct reply
escalated.

Both causes are the bucket being a model's decision:

- **The records bucket is written by the responder.** *What we already knew* is
  `draft.restated_facts` — the responder's own account of what came from our
  records. Code computed the figure, a model restated it, and grounding was
  asked to take the restatement on trust. It declined, and got the category
  wrong.
- **The exemption is a sentence in a prompt**, applied by a model to prose that
  paraphrases the list. Paraphrase in Malayalam is exactly where that judgment
  drifts.

Which bucket a figure belongs to is decided by where it came from, and only
code knows that — the pattern of §1.1. **Built, 11 September 2026:**
`ResponderContext.record_facts()` writes the facts from what code resolved and
computed. They are the reply's `restated_facts`, and they are what grounding is
told we already knew. `ResponderOutput` has no `restated_facts` field, so the
responder can neither put a figure in the records bucket nor leave one out; it
selects the facts he needs to hear in `text`, and it makes claims. A record
figure inside a claim is the responder ignoring its instructions, and
grounding's existing refusal covers it.

Code-written facts are said in his language through the table in §7.2, a
sentence at a time as its Malayalam is written.

---

## 6. Retrieval

- **`sop_chunks`** — per-customer standing instructions. Detention terms, free
  time, reattempt rules, gate procedure, site contacts, delivery windows.
- **`precedents`** — resolved exceptions and their outcome, written **only**
  when a human has marked the resolution approved. An unreviewed decision must
  never become the justification for the next one.

Retrieval query: see 6.1. Top-3 each.

### 6.1 What goes in the query, and the rule that decides it

**The retrieval query is the driver's words and nothing else** — his
transcript, plus the interpreter's plain-English rendering of his question
where he asked one. No ids, no timestamps, no stop fields, no event type, no
intent. `customer_id` selects the corpus in SQL and is not embedded.

That is narrower than it reads, and it follows from something general enough to
state on its own:

> **Anything added to every query is also in every noise probe.** Constant text
> raises the measured baseline exactly as fast as it raises a real question,
> and pulls every query toward the same point in the space, compressing the
> distance between them. A floor is `baseline + margin`. **Boilerplate spends
> headroom and buys no discrimination.**

This is not a remark about one bad field. It is a property of measuring a floor
from probes that travel through the same builder as the queries — which is the
only honest way to measure one, so the property is permanent. It holds for
anything constant: a template, a system preamble, a role line, a units hint, a
schema, a JSON envelope. It holds most treacherously for additions that are
*individually sensible*, because those are the ones that get added.

Two consequences worth naming, because neither is obvious from the rule:

- **The cost is invisible in the usual check.** Adding a field and confirming
  that real questions still score well is not evidence — the noise rose with
  them. Nothing is learned without measuring the noise under the same change,
  which is what `scripts/measure_margin.py` is for.
- **A calibration measured through a different builder than production is not
  a calibration.** If the probes and the live queries are assembled by
  different code paths, the floor describes a system that is not running. §5.1
  records what that cost here: real questions scoring under their own
  customer's floor, and on customer-3, under its measured noise.

The test for anything proposed for the query: **would it be in the query if a
noise probe were the input?** If yes, it is boilerplate and it does not go in.
The driver's words pass that test. The trip's timestamps do not.

§5.1 has the measurements, including what the event type costs when it is added
back — one short, defensible English phrase, about 0.04 of floor.

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

### 7.1 Traces

One Langfuse trace per inbound driver message, its id derived from the message
id. Under it: the interpreter, retrieval, responder and grounding calls, the
safety critic's assessment, identity resolution, and the router's decision —
mode, composite confidence, the four signals, the weakest of them, and why it
routed that way. A dispatcher's live safety check is a trace of its own. There
is no decision agent yet (§1, node 4); the decision code drafts is recorded with
the router's.

Tags: `trip:`, `stop:` and `exception:` where one exists, and `message:`. The
session is the trip.

- **Best effort, never load bearing.** Unreachable, misconfigured or raising,
  Langfuse changes nothing. Every SDK call is wrapped, and so is the code that
  builds what gets recorded. The suite runs one message with Langfuse off and
  with a client that raises on every call, and asserts the same reply, the same
  record and the same exceptions.
- **No `user_id`, ever.** Langfuse builds a per-user view out of it — a
  per-driver history by another name (CLAUDE.md rule 1). Traces attach to
  trips and messages, as exceptions do.
- **Its own database.** `docker compose --profile tracing up -d` runs Langfuse
  on its own Postgres. Traces and operational truth never share one.

### 7.2 Code's words to the driver

**Everything code says to a driver comes from one table, `domain/words.py`, by
language.** The event and stop words in his record of the day, the facts
restated with a model reply (§5.2), read-backs, the state machine's questions
when it disagrees with him, check-ins, and the escalation, failure and hedge
lines. The dispatcher's board, customer drafts, the responder's context and
traces stay English, and read the English column.

This is M4's `EVENT_WORDS` problem again, at the scale of every sentence code
writes. A Malayalam speaker who escalated — exactly when he most needs to
understand what is happening — heard English end to end, because every line
code wrote for him was English.

- **Whole sentences with named slots, per language.** Never English fragments
  stitched together: Malayalam word order is not English word order. A
  translation may drop a slot but never invent one, and the suite checks every
  written line against the slots its English offers.
- **One sentence, one language.** A sentence is said in his language if it,
  and every line composed into it — the status in "Stop 3: arrived", the minutes
  in "61 minutes so far" — is written. Otherwise the whole sentence is English.
  What went wrong before was mixing *inside* a sentence; sentences in different
  languages side by side are coherent. "Recorded: a; b." is one sentence, so an
  escalation's facts are his language only when every one of them can be; its
  closing is a sentence of its own.
- **Per line, not all or nothing.** A line is heard the moment it is written.
  The first version waited for all 61, which in practice gets them written fast
  and badly; per line lets the ones a driver hears most be written carefully
  first.
- **Never quote him back in a sentence of another language.** The Malayalam
  `fact.asked` has no `{question}` slot: it carries the interpreter's English.
- **A driver never hears a marker.** A line not yet written is said in
  English, whole.

**State, 11 September 2026:** English complete. Malayalam has 18 of 64 lines:
the three that already existed — the escalation opening and closing, and the
hedge — and 15 **unreviewed placeholders** for the lines a driver hears most,
written without a Malayalam speaker. They are marked in the table and listed in
`UNREVIEWED` until someone who speaks Malayalam has checked them. The rest are
`TODO`. The English loanwords in Malayalam script (ഫ്രീ ടൈം, ഡെലിവറി,
സ്റ്റോപ്പ്, മിനിറ്റ്, വിൻഡോ) are deliberate. Not covered: the driver page's own
labels and the composer's error messages.

---

### 7.3 Spoken replies — M6, first half

Text input stays as it is. `voice/tts.py` provides a TTS interface, initially
implemented by Sarvam Bulbul v3. `TTS_PROVIDER=sarvam` and `SARVAM_API_KEY`
enable it; either missing, an unsupported provider, or a failed call leaves
the reply as text.

Audio is a rendering step after routing. The driver page requests it after the
text renders, so neither the message pipeline nor the shift waits for TTS.
Only `DriverReply.text` is synthesised, unchanged. The separate facts and
claims lists remain on screen and are not appended to the spoken text.

The page plays available audio automatically, with a replay/pause control.
Browsers that refuse autoplay leave replay available by tap. Polling neither
restarts nor interrupts it; a new reply stops the old audio. Cache by reply id,
including concurrent requests and failures, for the life of the demo process.
Replay never re-synthesises.

The TTS call is a span under the original live message trace, linked by saved
trace and parent-span ids across the later audio request. Record latency and
whether it fell back to text. Trace failure still changes nothing. Captured
replay replies have no live message trace to attach to.

`scripts/check_tts.py` runs m06 through the live text pipeline, then TTS and a
cached replay. It saves the exact spoken text, WAV and timings for listening.
Successful synthesis alone does not establish pronunciation quality for the
Malayalam-English mixture; that needs listening against the text.

Measured 11 September 2026, one live m06: text 24.05 s, then TTS 5.76 s;
36.38 s of WAV audio, no text fallback. The prose mixed Malayalam with
`Kochi Homeware Distributors`, `10:12`, `10:13`, `10:00–12:00` and figures
including `40`, `60` and `59`. Cached replay: 0.006 ms, no synthesis.
Pronunciation has not yet been reviewed by listening.

The responder prompt was then shortened (§3.4), including its context's
instruction to recite the facts. Same m06, same TTS settings: **9.984 s** of
audio, TTS 2.93 s. The spoken text leads with the shut-gate clock rule, retains
his 40 minutes against our 1 minute counted from 10:12, and ends with the
router's office confirmation. One live sample; duration is a prompt target,
not a hard limit enforced by cutting audio or dropping safety content.

---

### 7.4 Spoken input — M6, second half

`voice/stt.py` provides an interface, initially Sarvam Saaras v3 in `codemix`
mode, configured by `STT_PROVIDER` and `SARVAM_API_KEY`. Recording supplements
typing. The driver starts the microphone explicitly, previews the note, then
sends it. Capture stops at 25 seconds; the API accepts at most 8 MB and Sarvam's
short-audio endpoint accepts up to 30 seconds. No transcript is repaired,
translated or padded before interpretation. Successful transcription enters
the existing text pipeline. Input audio is not retained by the demo server.

A failed STT call produces an explicit transcription error, no transcript,
no interpreter call and no trip event. The typed box remains available. A
successful transcription followed by interpreter failure is still §2.2: those
words reach a dispatcher as a processing failure. Completed message ids are
idempotent across retransmission, including the STT call.

STT is a span under the message trace, with latency, provider and genuine
transcription confidence (minimum across segments when supplied). The raw
confidence remains null when unavailable; §5 assigns zero credit. A later
dispatcher safety check retains that provenance. No language probability is
ever passed as transcription confidence.

**Provider limitation, 11 September 2026:** Sarvam's
[REST response](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe)
documents transcript, phrase timestamps, detected language and language
probability, but no transcription-confidence field. Therefore confidence/error
correlation cannot currently be measured for this adapter. That missing guard
is stated in §2.1; it is not worked around with a proxy.

**A candidate to compare:** Google Cloud documents Malayalam `ml-IN` on its
`short` model with word-level confidence in the
[language support table](https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-supported-languages).
Its [recognition response](https://docs.cloud.google.com/speech-to-text/docs/reference/rest/v2/projects.locations.recognizers/recognize)
describes genuine recognition confidence, but warns it is not guaranteed
accurate or always supplied; zero can mean unset. A future adapter must honour
that sentinel. Having the field does not establish calibration on noisy
Malayalam-English. Not integrated or measured here.

**First human-recording comparison, 12 September 2026:** five notes (01, 02,
03, 05, 06), one female speaker in clean conditions. All five transcripts
produced the expected intent/event reading; note 01 retained the 40-minute
wait. Note 02 is the first observed instance of §2.1 outside the constructed
test: the speaker confirms the shop-closure phrase is garbled, yet the
interpreter classified consignee absence correctly despite the wrong
transcript. The verbatim line is pending; the observation and its safety
implication are recorded in §2.1. The
English references are used as meaning-level ground truth, not verified
verbatim Malayalam: word-error rate is unavailable. Different input languages
also prevent the reference/transcript comparison alone from isolating ASR
damage. Mean STT latency was 1.31 s (range 1.00–1.96 s), excluding downstream
processing. Genuine transcription confidence was absent on all five calls.

**Follow-up note 04, same day:** Sarvam returned `കഴിഞ്ഞു.`; Haiku chose
`STOP_COMPLETED` with no unresolved fields, while the supplied ambiguous
`Over.` reference produced `UNCLEAR`. This is the separate STT ambiguity-collapse
finding (§2.4), with the attribution limits recorded there,
bringing the set to five expected readings out of six. STT took 0.668 s and
again supplied no transcription confidence. Evidence is in
`recordings/stt-results/2026-09-12-note04/`; the combined comparison is in
`recordings/stt-results/2026-09-12-six-notes/`. It does not isolate ASR damage
from the interpreter's treatment of the different languages.

This supports further supervised testing, not field reliability. Route
drivers are overwhelmingly male and audio will be worse: this sample is an
optimistic clean-condition ceiling, not a measurement of route-driver
performance. Engine noise and the opened-gate contrast remain untested. The
originally planned four voice notes in STACK.md did not
exist. `scripts/check_stt.py` saves raw responses and compares interpreter
readings; WER requires verbatim references. Local evidence is in
`recordings/stt-results/2026-09-12/`. No synthesised note substitutes for a
human recording in this evaluation.

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
