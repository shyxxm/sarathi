from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import cached_property
import json
import hashlib
import logging
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app import tracing
from app.agents import responder, router, safety_critic
from app.contracts.decision import ActionType, Decision, DecisionAction
from app.contracts.enums import EventType, Intent, Language, ReplyMode
from app.contracts.event import InterpreterOutput, OperationalEvent
from app.contracts.reply import Claim, DriverReply
from app.contracts.retrieval import RetrievalResult, RetrievedChunk
from app.domain import detention, exception_rules, identity, resolution, watchdog, words
from app.domain.state_machine import EVENT_WORDS, FINISHED, Rejected, TripState, apply, left_a_stop
from app.voice.stt import SpeechInput, Transcription

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger(__name__)
MINUTE = timedelta(minutes=1)


def event_facts(state: TripState, event: OperationalEvent) -> list[str]:
    """His record of one event, each sentence in his language where it is written."""
    lang = state.driver_language
    stop = state.stop(event.stop_id)
    happened = words.part(f"event.{event.event_type.value}")
    if event.event_type is EventType.DRIVER_SILENT:
        # A question on its own, not an event with a full stop after it.
        facts = [words.say(lang, "record.check_in")]
    elif event.event_type is EventType.DEPARTED and stop:
        # "You set off — stop 1" meant both "left the depot for stop 1" and
        # "left stop 1", so he could not correct it. Code knows which (SPEC 3.1).
        facts = [words.say(lang, "record.departed_from" if left_a_stop(state, event) else "record.departed_to",
                           seq=stop.seq, customer=state.customer_for(stop.id).name)]
    elif stop:
        facts = [words.say(lang, "record.event_at_stop", event=happened, seq=stop.seq,
                           customer=state.customer_for(stop.id).name)]
    else:
        facts = [words.say(lang, "record.event", event=happened)]
    if event.event_type is EventType.ARRIVED_STOP:
        facts.append(words.say(lang, "record.arrival", time=f"{event.ingested_at:%H:%M}"))
    if event.driver_claimed_wait_minutes is not None:
        facts.append(words.say(lang, "record.claimed_wait",
                               minutes=words.minutes(event.driver_claimed_wait_minutes)))
    return facts


def waiting_at(state: TripState, stop_id: str | None, now: datetime):
    if stop_id is None:
        return None
    return detention.compute(state, stop_id, detention.wait_ended_at(state, stop_id) or now)


def record(state: TripState, event: OperationalEvent, now: datetime):
    updated, outcome = apply(state, event, now)
    if isinstance(outcome, Rejected):
        return state, outcome
    updated = updated.with_exceptions(exception_rules.evaluate(updated, event, now).changed)
    return updated.with_exceptions(resolution.close_matching(updated, event, now)), outcome


def _trace_route(trace, exchange, why, state=None, stop_id=None):
    """SPEC 7.1: the router's decision, on the trace. When a reply looks wrong
    this is the first thing anyone wants — what it routed on, and why."""
    def decision():
        assessment = exchange.assessment
        fields = {"mode": exchange.reply.mode.value, "why": why}
        if assessment is not None:
            fields.update(
                confidence=assessment.confidence, risk=assessment.risk, signals=assessment.signals,
                weakest=min(assessment.signals, key=assessment.signals.get),
                grounding_ran=assessment.grounding_ran,
                grounded_claims=[claim.text for claim in assessment.grounded_claims],
                rejected_claims=[{"claim": claim.text, "why": reason}
                                 for claim, reason in assessment.rejected_claims],
                unclaimed_assertions=list(assessment.unclaimed_assertions),
            )
        opened = next((ex for ex in (state.exceptions if state else ())
                       if ex.stop_id == stop_id and resolution.is_open(ex) and ex.decision), None)
        if opened:
            fields["decision"] = {"actions": [action.type.value for action in opened.decision.actions],
                                  "by": "code; there is no decision agent yet"}
        return fields

    tracing.event("router", lambda: {"output": decision()})
    trace.finish(lambda: {"output": exchange.reply.text, "metadata": {"router": decision()}})


@dataclass(frozen=True)
class Exchange:
    id: str
    at: datetime
    transcript: str | None
    reply: DriverReply
    chunks: tuple[RetrievedChunk, ...] = ()
    assessment: safety_critic.Assessment | None = None
    interpretation: InterpreterOutput | None = None
    retrieval: RetrievalResult | None = None
    origin: str = "live"
    # Set when the message never became a reading. SPEC 2.2: a processing
    # failure for a person to answer, not an operational exception.
    failure: str | None = None
    transcription: Transcription | None = None


@dataclass
class Approval:
    id: str
    exception_id: str
    customer: str
    text: str
    created_at: datetime
    status: str = "PENDING"
    reviewed_at: datetime | None = None


class MessageUnavailable(Exception):
    pass


# SPEC 2.2. What he is told is `failed.*` in `words`: the fault is ours. "I
# couldn't read your message" was said about perfectly clean English, and it is
# a false statement about him. The board's line stays English.
INTERPRETER_FAILED_BOARD = "Sarathi could not interpret this message. Nothing was recorded on the trip."


class ShiftService:
    """One process-local demo shift; replay snapshots never mutate that shift."""

    def __init__(self, *, start_minute: int = 193):
        seed = ROOT / "data" / "seed"
        trip = json.loads((seed / "trips.json").read_text())[0]
        customers = json.loads((seed / "customers.json").read_text())
        drivers = json.loads((seed / "drivers.json").read_text())
        self.initial = TripState.build(trip, trip["stops"], customers, drivers)
        self.fixtures = tuple(OperationalEvent.model_validate(row) for row in json.loads(
            (ROOT / "eval" / "fixtures" / "fixture_events.json").read_text()))
        self.lock = RLock()
        self.minute = start_minute
        self.state = self.replay(start_minute)
        self.exchanges: list[Exchange] = []
        self.approvals: dict[str, Approval] = {}
        self.pending_messages: set[str] = set()
        self.completed_messages: set[str] = set()
        self.reply_trace_parents: dict[str, dict] = {}
        self.revision = 0
        self._queue_drafts()

    @cached_property
    def captured_exchanges(self) -> dict[str, Exchange]:
        path = ROOT / "data/seed/replay_assessments.json"
        if not path.exists():
            return {}
        payload = json.loads(path.read_text())
        digest = hashlib.sha256((ROOT / "eval/fixtures/fixture_events.json").read_bytes()).hexdigest()
        if payload["fixture_sha256"] != digest:
            return {}
        result = {}
        for row in payload["exchanges"]:
            values = row["assessment"]
            values["grounded_claims"] = tuple(Claim.model_validate(c) for c in values["grounded_claims"])
            values["rejected_claims"] = tuple((Claim.model_validate(c), why) for c, why in values["rejected_claims"])
            values["unclaimed_assertions"] = tuple(values["unclaimed_assertions"])
            result[row["event_id"]] = Exchange(
                row["event_id"], datetime.fromisoformat(row["at"]), None,
                DriverReply.model_validate(row["reply"]),
                tuple(RetrievedChunk.model_validate(c) for c in row["chunks"]),
                safety_critic.Assessment(**values), origin="captured replay",
            )
        return result

    def recorded_exchanges(self, state):
        result = []
        for event in state.events:
            captured = self.captured_exchanges.get(event.id)
            if captured:
                result.append(Exchange(event.id, captured.at, event.raw_transcript,
                                       captured.reply, captured.chunks, captured.assessment,
                                       origin=captured.origin))
            else:
                facts = event_facts(state, event)
                result.append(Exchange(event.id, event.ingested_at, event.raw_transcript,
                                       DriverReply(mode=ReplyMode.SPEAK,
                                                   language=words.language_of(facts),
                                                   text=" ".join(facts), restated_facts=facts),
                                       origin="state read-back"))
        return result

    @cached_property
    def snapshots(self) -> tuple[TripState, ...]:
        scheduled = defaultdict(list)
        for event in self.fixtures:
            scheduled[event.ingested_at].append(event)
        state, snapshots = self.initial, []
        for minute in range(601):
            now = self.initial.shift_start + minute * MINUTE
            for event in scheduled[now]:
                state, outcome = record(state, event, now)
                if isinstance(outcome, Rejected):
                    raise ValueError(f"Invalid fixture {event.id}: {outcome.reason}")
            for event in watchdog.tick(state, now):
                state, _ = record(state, event, now)
            for exception in state.exceptions:
                captured = self.captured_exchanges.get(exception.opening_event_id)
                if captured and exception.opened_at == now:
                    assessment = captured.assessment
                    state = state.with_exceptions([exception.model_copy(update={
                        "confidence": assessment.confidence, "risk": assessment.risk,
                        "reply_mode": captured.reply.mode, "driver_informed": True,
                        "decision": (Decision(actions=[DecisionAction(type=ActionType.DRAFT_CUSTOMER_MESSAGE)],
                                             rationale="Ask the customer for the next step; a dispatcher must review the draft.")
                                     if exception.stop_id else None),
                        "audit": [*exception.audit, {"action": "REPLY", "at": now.isoformat(),
                                  "exchange_id": captured.id, "signals": assessment.signals,
                                  "grounding_ran": assessment.grounding_ran}],
                    })])
            state = state.with_exceptions(resolution.expire_open(state, now))
            snapshots.append(state)
        return tuple(snapshots)

    def replay(self, minute: int) -> TripState:
        if not 0 <= minute <= 600:
            raise ValueError("Replay must be between 08:00 and 18:00")
        return self.snapshots[minute]

    @property
    def now(self) -> datetime:
        return self.initial.shift_start + self.minute * MINUTE

    def advance(self, minutes: int = 1):
        with self.lock:
            for _ in range(min(minutes, 600 - self.minute)):
                self.minute += 1
                for event in watchdog.tick(self.state, self.now):
                    self.state, _ = record(self.state, event, self.now)
                    facts = event_facts(self.state, event)
                    self.exchanges.append(Exchange(
                        id=event.id, at=self.now, transcript=None,
                        reply=DriverReply(mode=ReplyMode.SPEAK,
                                          language=words.language_of(facts),
                                          text=" ".join(facts), restated_facts=facts),
                    ))
                    self._mark_informed(event.stop_id)
                self.state = self.state.with_exceptions(resolution.expire_open(self.state, self.now))
            self._queue_drafts()
            self.revision += 1

    def _queue_drafts(self):
        for exception in self.state.exceptions:
            if not resolution.is_open(exception) or exception.stop_id is None:
                continue
            key = f"draft-{exception.id}"
            if key in self.approvals:
                continue
            stop = self.state.stop(exception.stop_id)
            customer = self.state.customer_for(stop.id)
            text = (f"Delivery update for {customer.name}, stop {stop.seq}: "
                    f"{EVENT_WORDS[exception.exception_type].removesuffix('.')} "
                    f"(recorded at {exception.opened_at:%H:%M}). "
                    "Please confirm the next step with the dispatcher.")
            self.approvals[key] = Approval(key, exception.id, customer.name, text, self.now)
            decision = Decision(
                actions=[DecisionAction(type=ActionType.DRAFT_CUSTOMER_MESSAGE,
                                        payload={"text": text})],
                rationale="Ask the customer for the next step; a dispatcher must review the draft.",
            )
            self.state = self.state.with_exceptions([exception.model_copy(update={"decision": decision})])

    def review(self, draft_id: str, action: str):
        with self.lock:
            approval = self.approvals.get(draft_id)
            if approval is None:
                raise KeyError(draft_id)
            if action not in {"APPROVED", "REJECTED"}:
                raise ValueError("Choose approve or reject")
            if approval.status != "PENDING":
                if approval.status == action:
                    return
                raise ValueError("This draft has already been reviewed")
            exception = next(ex for ex in self.state.exceptions if ex.id == approval.exception_id)
            if not resolution.is_open(exception):
                raise ValueError("This exception has closed; its draft is no longer current")
            approval.status = action
            approval.reviewed_at = self.now
            self.state = self.state.with_exceptions([exception.model_copy(update={
                "audit": [*exception.audit, {"at": self.now.isoformat(),
                          "action": f"CUSTOMER_DRAFT_{action}", "draft_id": draft_id}],
            })])
            self.revision += 1

    def _mark_informed(self, stop_id):
        self.state = self.state.with_exceptions([
            ex.model_copy(update={"driver_informed": True}) for ex in self.state.exceptions
            if ex.stop_id == stop_id and resolution.is_open(ex)
        ])

    def retrieve(self, state, understood, event):
        stop = state.stop(event.stop_id)
        if stop is None:
            return RetrievalResult()
        if not hasattr(self, "_retriever"):
            from sqlalchemy import create_engine
            from app.retrieval.embed import LiteLLMEmbedder
            from app.retrieval.retriever import Retriever
            from dotenv import load_dotenv

            load_dotenv()
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise MessageUnavailable("The customer's rules could not be loaded")
            self._retriever = Retriever(create_engine(database_url), LiteLLMEmbedder.from_env())
        # His words only. The resolved stop is not handed over: it is how code
        # found the corpus, not anything he said, and embedding it cost more
        # than the whole citation margin (SPEC 5.1).
        situation = event.raw_transcript or EVENT_WORDS[event.event_type]
        with tracing.observe("retrieval", as_type="retriever",
                             input={"situation": situation, "question": understood.question_text}) as span:
            result = self._retriever.retrieve(
                customer_id=stop.customer_id, situation=situation, question=understood.question_text,
            )
            span.record(lambda: {"output": {
                "relevance_floor": result.relevance_floor, "retrieval_score": result.retrieval_score,
                "sop_chunks": [{"id": chunk.id, "score": round(chunk.score, 4)} for chunk in result.sop_chunks],
                "cited": [chunk.id for chunk in result.cited_sop_chunks],
            }})
            return result

    def interpret(self, text):
        from app.agents.interpreter import interpret

        return interpret(text)

    def _answer(self, state, event, understood, now, *, transcription=None) -> Exchange:
        stop = state.stop(event.stop_id)
        exception = next((ex for ex in state.exceptions
                          if ex.stop_id == event.stop_id and resolution.is_open(ex)), None)
        retrieval = self.retrieve(state, understood, event)
        context = responder.assemble(
            now=now, understood=understood, transcript=event.raw_transcript, stop=stop,
            language=state.driver_language,
            customer=state.customer_for(stop.id) if stop else None,
            retrieval=retrieval, detention=waiting_at(state, event.stop_id, now), exception=exception,
        )
        draft = responder.respond(context)
        # Code's facts, not the draft's: which figures are ours is a question
        # about state (SPEC 5.2). Grounding is told them, and he is shown them.
        records = context.record_facts()
        assessment = safety_critic.assess(
            understood=understood, draft=draft, chunks=context.sop_chunks, records=records,
            retrieval_score=retrieval.retrieval_score,
            # Missing voice confidence earns no credit. None retains the
            # existing typed-text behaviour; it must not stand in for voice.
            stt_confidence=(transcription.stt_confidence or 0.0) if transcription else None,
            cost_exposure_paise=detention.exposure_paise(state, event.stop_id, now),
            decision=exception.decision if exception else None,
        )
        # A provider failure is never a grounding pass, even for a claimless draft.
        if not assessment.grounding_ran:
            raise MessageUnavailable("The safety check could not be completed")
        return Exchange(event.id, now, event.raw_transcript,
                        router.route(draft, assessment, understood=understood,
                                     language=state.driver_language, facts=records),
                        context.sop_chunks, assessment, understood, retrieval)

    def submit(self, text: str, message_id: str):
        self._submit(message_id, text=text)

    def submit_voice(self, audio: bytes, media_type: str, message_id: str, speech: SpeechInput):
        self._submit(message_id, audio=audio, media_type=media_type, speech=speech)

    def _submit(self, message_id, *, text=None, audio=None, media_type=None, speech=None):
        with self.lock:
            if message_id in self.completed_messages:
                return
            if message_id in self.pending_messages:
                raise MessageUnavailable("This message is still being read. Please wait.")
            if self.minute >= 600:
                raise MessageUnavailable("Today's shift has ended. This message has not been recorded.")
            self.pending_messages.add(message_id)
            state, now, revision = self.state, self.now, self.revision
        try:
            # One trace per message, found by its id (SPEC 7.1). `tracing`
            # swallows its own failures: this cannot change what happens to
            # his message.
            trace_input = text if audio is None else {"source": "voice", "audio_bytes": len(audio),
                                                     "media_type": media_type}
            with tracing.trace("driver message", seed=message_id, input=trace_input,
                               trip_id=state.trip_id, message_id=message_id) as trace:
                transcription = speech.transcribe(audio, media_type) if audio is not None else None
                if transcription:
                    text = transcription.text
                self._process(text, message_id, state, now, revision, trace, transcription=transcription)
        finally:
            with self.lock:
                self.pending_messages.discard(message_id)

    def _process(self, text, message_id, state, now, revision, trace, *, transcription=None):
        try:
            understood = self.interpret(text)
        except Exception as error:
            # Escalated, not retried: a model answering in prose does it
            # again at temperature 0, and his question reaches nobody. The
            # trip is untouched, so no revision check. SPEC 2.2.
            LOG.warning("Interpreter failed on message %s; escalating", message_id, exc_info=True)
            exchange = replace(self._failed(text, message_id, now, state), transcription=transcription)
            _trace_route(trace, exchange, f"interpreter failed ({type(error).__name__}): "
                                          "processing failure, sent to a dispatcher")
            with self.lock:
                self.exchanges.append(exchange)
                if parent := trace.parent():
                    self.reply_trace_parents[exchange.id] = parent
                self.completed_messages.add(message_id)
            return
        # SPEC 1's two branches. A message that reports nothing is not
        # operational progress, so it carries no event — and a message with
        # no event in it has not failed to be understood. Decided before
        # identity resolution rather than after, so the resolver is looking
        # at the event type this message actually has.
        reported = Intent.REPORT in understood.intents
        event_type = (understood.event_type or EventType.UNCLEAR) if reported \
            else EventType.ACKNOWLEDGEMENT
        event = identity.resolve(OperationalEvent(
            id=f"message-{message_id}", source_message_id=message_id, source="driver",
            occurred_at=now, ingested_at=now, raw_transcript=text,
            event_type=event_type,
            language=understood.language, location_hint=understood.location_hint,
            driver_claimed_wait_minutes=understood.driver_claimed_wait_minutes,
            unresolved_fields=list(understood.unresolved_fields),
        ), state)
        # Departing a completed stop is resolved from the last report, before the
        # resolver's next-pending-stop fallback can mistake it for depot departure.
        last = state.last_driver_event()
        if (event.event_type is EventType.DEPARTED and last and not event.location_hint
                and state.stop(last.stop_id) and state.stop(last.stop_id).status in FINISHED):
            event = event.model_copy(update={"stop_id": last.stop_id})
        # Each of these is a fact about the message and nothing else. The
        # sentence saying a person is looking at it is added once, by the
        # router, in `_fallback` — not here as well.
        lang = state.driver_language
        issue = None
        if Intent.CORRECTION in understood.intents or understood.contradicts_recent_state:
            issue = words.say(lang, "issue.correction")
        elif event.unresolved_fields or not understood.transcript_legible:
            issue = words.say(lang, "issue.unclear")
        if issue:
            event = event.model_copy(update={"event_type": EventType.UNCLEAR})
        updated, outcome = record(state, event, now)
        if isinstance(outcome, Rejected):
            issue = outcome.reason
            event = outcome.unclear_event
            updated, _ = record(state, event, now)
        understood = understood.model_copy(update={"unresolved_fields": list(event.unresolved_fields)})
        tracing.event("identity", lambda: {"output": {
            "event_type": event.event_type.value, "stop_id": event.stop_id,
            "unresolved_fields": list(event.unresolved_fields), "issue": issue}})
        trace.tag(lambda: {"stop_id": event.stop_id, "exception_id": next(
            (ex.id for ex in updated.exceptions if ex.stop_id == event.stop_id and resolution.is_open(ex)), None)})
        if issue:
            exchange = self._fallback(event, now, [issue], lang)
            why = "escalated without a model reply: the message needs a person"
        elif event.event_type not in exception_rules.OPENS and Intent.QUESTION not in understood.intents:
            facts = event_facts(updated, event)
            exchange = Exchange(event.id, now, text, DriverReply(
                mode=ReplyMode.SPEAK, language=words.language_of(facts),
                text=" ".join(facts), restated_facts=facts))
            why = "read back from the record; no model reply needed"
        else:
            # Queue the proposed outbound action in the critic's context so it
            # contributes HIGH risk before routing. Nothing is sent here.
            updated = updated.with_exceptions([
                ex.model_copy(update={"decision": ex.decision or Decision(
                    actions=[DecisionAction(type=ActionType.DRAFT_CUSTOMER_MESSAGE)],
                    rationale="Dispatcher review required before contacting the customer.")})
                for ex in updated.exceptions if resolution.is_open(ex) and ex.stop_id == event.stop_id
            ])
            try:
                exchange = self._answer(updated, event, understood, now, transcription=transcription)
                why = "routed on the safety critic's assessment"
            except Exception as error:
                LOG.warning("Reply services unavailable; retaining the report and escalating", exc_info=True)
                exchange = self._fallback(event, now, event_facts(updated, event), lang)
                why = f"reply services failed ({type(error).__name__}): report kept, escalated"
        exchange = replace(exchange, transcription=transcription)
        _trace_route(trace, exchange, why, updated, event.stop_id)
        with self.lock:
            if self.revision != revision:
                raise MessageUnavailable("The shift changed while I read this. Your message has not been recorded. Please send it again.")
            self.state = updated
            self._save_exchange(exchange, event.stop_id)
            if parent := trace.parent():
                self.reply_trace_parents[exchange.id] = parent
            self._queue_drafts()
            self.completed_messages.add(message_id)
            self.revision += 1

    def _fallback(self, event, now, facts, language):
        """Escalate without a model: the interpreter or the reply services are
        down, or the message was not legible enough to act on.

        Worded by the router, not here. Two places writing "I'll check with the
        office" is how the driver came to hear it twice in one reply. `language`
        is his; the facts sentence is said in it only if every fact can be
        (SPEC 7.2).
        """
        return Exchange(event.id, now, event.raw_transcript, DriverReply(
            mode=ReplyMode.ESCALATE, language=words.language_of(facts), restated_facts=facts,
            text=router.escalation_text(facts, language),
        ))

    def _failed(self, text, message_id, now, state):
        """The interpreter failed. His raw text goes to a dispatcher; nothing
        goes on the trip. Not through `_save_exchange`: it would attach this to
        any open exception with no stop, and it is not about one."""
        lang = state.driver_language
        facts = [words.say(lang, "failed.reached"), words.say(lang, "failed.nothing_recorded")]
        return Exchange(f"message-{message_id}", now, text, DriverReply(
            mode=ReplyMode.ESCALATE, language=words.language_of(facts), restated_facts=facts,
            text=router.failure_text(facts, lang),
        ), failure=INTERPRETER_FAILED_BOARD)

    def _save_exchange(self, exchange, stop_id):
        self.exchanges.append(exchange)
        assessment = exchange.assessment
        for ex in self.state.exceptions:
            if ex.stop_id != stop_id or not resolution.is_open(ex):
                continue
            audit = {"action": "REPLY", "at": exchange.at.isoformat(), "exchange_id": exchange.id,
                     "signals": assessment.signals if assessment else None,
                     "grounding_ran": assessment.grounding_ran if assessment else False}
            self.state = self.state.with_exceptions([ex.model_copy(update={
                "driver_informed": True, "reply_mode": exchange.reply.mode,
                "confidence": assessment.confidence if assessment else None,
                "risk": assessment.risk if assessment else None, "audit": [*ex.audit, audit],
            })])

    def assess_exception(self, exception_id):
        with self.lock:
            state, now, revision = self.state, self.now, self.revision
            exception = next((ex for ex in state.exceptions if ex.id == exception_id), None)
            if exception is None:
                raise KeyError(exception_id)
            if not resolution.is_open(exception):
                raise MessageUnavailable("This exception has already closed.")
            event = next(e for e in state.events if e.id == exception.opening_event_id)
            original = next((e for e in reversed(self.exchanges) if e.id == event.id), None)
            transcription = original.transcription if original else None
        understood = InterpreterOutput(
            intents=[Intent.REPORT], language=event.language or Language.EN,
            event_type=event.event_type, driver_claimed_wait_minutes=event.driver_claimed_wait_minutes,
        )
        check_id = str(uuid4())
        with tracing.trace("exception check", seed=check_id,
                           input=event.raw_transcript or EVENT_WORDS[event.event_type],
                           trip_id=state.trip_id, stop_id=exception.stop_id,
                           exception_id=exception.id) as trace:
            try:
                exchange = self._answer(state, event, understood, now, transcription=transcription)
            except Exception:
                # The dispatcher sees the short sentence; the cause goes to the
                # log. `from None` alone hid a refused draft behind "check the
                # configured models".
                LOG.warning("Live safety check failed for %s", exception_id, exc_info=True)
                raise MessageUnavailable("The reply could not be checked. Check the configured models and indexed SOPs, then try again.") from None
            _trace_route(trace, exchange, "a dispatcher ran a live safety check", state, exception.stop_id)
            parent = trace.parent()
        exchange = Exchange(check_id, now, exchange.transcript, exchange.reply,
                            exchange.chunks, exchange.assessment, understood, exchange.retrieval,
                            transcription=transcription)
        with self.lock:
            if revision != self.revision:
                raise MessageUnavailable("The shift changed during the check. Please try again.")
            self._save_exchange(exchange, exception.stop_id)
            if parent:
                self.reply_trace_parents[exchange.id] = parent
            self.revision += 1
