from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property
import json
import logging
import os
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.agents import responder, router, safety_critic
from app.contracts.decision import ActionType, Decision, DecisionAction
from app.contracts.enums import EventType, Intent, Language, ReplyMode
from app.contracts.event import InterpreterOutput, OperationalEvent
from app.contracts.reply import DriverReply
from app.contracts.retrieval import RetrievalResult, RetrievedChunk
from app.domain import detention, exception_rules, identity, resolution, watchdog
from app.domain.state_machine import FINISHED, Rejected, TripState, apply

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger(__name__)
MINUTE = timedelta(minutes=1)

EVENT_WORDS = {
    EventType.DEPARTED: "You set off",
    EventType.ARRIVED_STOP: "You arrived",
    EventType.SERVICE_STARTED: "Unloading started",
    EventType.STOP_COMPLETED: "Delivery completed",
    EventType.GATE_CLOSED: "The gate is closed",
    EventType.CONSIGNEE_ABSENT: "Nobody is there to receive the delivery",
    EventType.VEHICLE_BREAKDOWN: "The vehicle has broken down",
    EventType.DOCUMENT_ISSUE: "There is a problem with the paperwork",
    EventType.SHORTAGE_OR_DAMAGE: "A shortage or damage was reported",
    EventType.DELIVERY_REFUSED: "The delivery was refused",
    EventType.ACKNOWLEDGEMENT: "Your message was received",
    EventType.UNCLEAR: "Your message needs clarification",
    EventType.STOP_OVERDUE: "We checked whether you need help reaching the stop",
    EventType.DRIVER_SILENT: "Everything alright? Need anything?",
    EventType.WINDOW_AT_RISK: "The delivery window may be missed",
    EventType.DETENTION_CROSSED: "The recorded wait passed the free allowance",
    EventType.REATTEMPT_SCHEDULED: "Another delivery attempt was scheduled",
}


def event_facts(state: TripState, event: OperationalEvent) -> list[str]:
    stop = state.stop(event.stop_id)
    where = f" — stop {stop.seq}, {state.customer_for(stop.id).name}" if stop else ""
    facts = [f"{EVENT_WORDS[event.event_type]}{where}."]
    if event.event_type is EventType.ARRIVED_STOP:
        facts.append(f"Your arrival was recorded at {event.ingested_at:%H:%M}.")
    if event.driver_claimed_wait_minutes is not None:
        facts.append(f"You said you had waited about {event.driver_claimed_wait_minutes} minutes.")
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


@dataclass(frozen=True)
class Exchange:
    id: str
    at: datetime
    transcript: str | None
    reply: DriverReply
    chunks: tuple[RetrievedChunk, ...] = ()
    assessment: safety_critic.Assessment | None = None


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
        self.revision = 0
        self._queue_drafts()

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
                        reply=DriverReply(mode=ReplyMode.SPEAK, language=Language.EN,
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
        return self._retriever.retrieve(
            customer_id=stop.customer_id,
            situation=event.raw_transcript or EVENT_WORDS[event.event_type],
            question=understood.question_text,
        )

    def interpret(self, text):
        from app.agents.interpreter import interpret

        return interpret(text)

    def _answer(self, state, event, understood, now) -> Exchange:
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
        assessment = safety_critic.assess(
            understood=understood, draft=draft, chunks=context.sop_chunks,
            retrieval_score=retrieval.retrieval_score,
            cost_exposure_paise=detention.exposure_paise(state, event.stop_id, now),
            decision=exception.decision if exception else None,
        )
        # A provider failure is never a grounding pass, even for a claimless draft.
        if not assessment.grounding_ran:
            raise MessageUnavailable("The safety check could not be completed")
        return Exchange(event.id, now, event.raw_transcript,
                        router.route(draft, assessment, understood=understood,
                                     language=state.driver_language),
                        context.sop_chunks, assessment)

    def submit(self, text: str, message_id: str):
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
            try:
                understood = self.interpret(text)
            except Exception:
                LOG.warning("Interpreter unavailable", exc_info=False)
                raise MessageUnavailable("I couldn't read your message just now. It has not been recorded. Please try again.") from None
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
            issue = None
            if Intent.CORRECTION in understood.intents or understood.contradicts_recent_state:
                issue = "You asked to correct the record. Your earlier record is unchanged for now."
            elif event.unresolved_fields or not understood.transcript_legible:
                issue = "I could not make out what happened or which stop this is about."
            if issue:
                event = event.model_copy(update={"event_type": EventType.UNCLEAR})
            updated, outcome = record(state, event, now)
            if isinstance(outcome, Rejected):
                issue = outcome.reason
                event = outcome.unclear_event
                updated, _ = record(state, event, now)
            understood = understood.model_copy(update={"unresolved_fields": list(event.unresolved_fields)})
            if issue:
                exchange = self._fallback(event, now, [issue])
            elif event.event_type not in exception_rules.OPENS and Intent.QUESTION not in understood.intents:
                facts = event_facts(updated, event)
                exchange = Exchange(event.id, now, text, DriverReply(
                    mode=ReplyMode.SPEAK, language=Language.EN,
                    text=" ".join(facts), restated_facts=facts))
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
                    exchange = self._answer(updated, event, understood, now)
                except Exception:
                    LOG.warning("Reply services unavailable; retaining the report and escalating", exc_info=False)
                    exchange = self._fallback(event, now, event_facts(updated, event))
            with self.lock:
                if self.revision != revision:
                    raise MessageUnavailable("The shift changed while I read this. Your message has not been recorded. Please send it again.")
                self.state = updated
                self._save_exchange(exchange, event.stop_id)
                self._queue_drafts()
                self.completed_messages.add(message_id)
                self.revision += 1
        finally:
            with self.lock:
                self.pending_messages.discard(message_id)

    def _fallback(self, event, now, facts):
        """Escalate without a model: the interpreter or the reply services are
        down, or the message was not legible enough to act on.

        Worded by the router, not here. Two places writing "I'll check with the
        office" is how the driver came to hear it twice in one reply, and the
        facts themselves are already English, so this speaks English rather
        than wrapping English facts in his language.
        """
        return Exchange(event.id, now, event.raw_transcript, DriverReply(
            mode=ReplyMode.ESCALATE, language=Language.EN, restated_facts=facts,
            text=router.escalation_text(facts, Language.EN),
        ))

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
        understood = InterpreterOutput(
            intents=[Intent.REPORT], language=event.language or Language.EN,
            event_type=event.event_type, driver_claimed_wait_minutes=event.driver_claimed_wait_minutes,
        )
        try:
            exchange = self._answer(state, event, understood, now)
        except Exception:
            raise MessageUnavailable("The reply could not be checked. Check the configured models and indexed SOPs, then try again.") from None
        exchange = Exchange(str(uuid4()), now, exchange.transcript, exchange.reply,
                            exchange.chunks, exchange.assessment)
        with self.lock:
            if revision != self.revision:
                raise MessageUnavailable("The shift changed during the check. Please try again.")
            self._save_exchange(exchange, exception.stop_id)
            self.revision += 1
