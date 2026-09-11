from dataclasses import asdict
from uuid import uuid4

from app.agents.safety_critic import WEIGHTS
from app.contracts.enums import ExceptionStatus, ReplyMode
from app.domain import detention, resolution
from app.domain.state_machine import STATUS_PHRASE
from app.api.service import EVENT_WORDS, MINUTE, event_facts, waiting_at

SIGNAL_NAMES = {
    "entity_resolution": "Entity resolution",
    "transcript_legible": "Transcript legibility",
    "retrieval_score": "SOP retrieval",
    "self_rating": "Model self-rating",
}


def context(service, *, minute=None):
    with service.lock:
        replay = minute is not None
        state = service.replay(minute) if replay else service.state
        now = state.shift_start + minute * MINUTE if replay else service.now
        exchanges = [] if replay else list(service.exchanges)
        latest = exchanges[-1] if exchanges else None
        last_event = state.last_driver_event()
        record = [{"id": event.id, "at": event.ingested_at, "occurred_at": event.occurred_at,
                   "facts": event_facts(state, event), "source": event.source}
                  for event in reversed(state.events)]
        attention, handled = [], []
        by_exchange = {exchange.id: exchange for exchange in exchanges}
        for exception in reversed(state.exceptions):
            stop = state.stop(exception.stop_id)
            audit = next((entry for entry in reversed(exception.audit) if entry["action"] == "REPLY"), {})
            exchange = by_exchange.get(audit.get("exchange_id"))
            card = {
                "exception": exception, "title": EVENT_WORDS[exception.exception_type],
                "stop": stop, "customer": state.customer_for(stop.id) if stop else None,
                "waiting": waiting_at(state, exception.stop_id, now),
                "claimed_wait": detention.claimed_wait_minutes(state, exception.stop_id) if stop else None,
                "signals": [{"name": SIGNAL_NAMES[key], "key": key, "weight": weight,
                             "value": (audit.get("signals") or {}).get(key)} for key, weight in WEIGHTS.items()],
                "assessment": exchange.assessment if exchange else None,
                "assessed_at": exchange.at if exchange else None,
            }
            (handled if exception.status is ExceptionStatus.RESOLVED else attention).append(card)
        troubled = {ex.stop_id for ex in state.exceptions if resolution.is_open(ex) or
                    ex.status is ExceptionStatus.EXPIRED}
        fine = [{"stop": stop, "customer": state.customer_for(stop.id),
                 "status": STATUS_PHRASE[stop.status]} for stop in state.stops if stop.id not in troubled]
        approvals = []
        if not replay:
            for approval in service.approvals.values():
                row = asdict(approval)
                exception = next(ex for ex in state.exceptions if ex.id == approval.exception_id)
                row["stale"] = not resolution.is_open(exception)
                approvals.append(row)
        linked = {entry.get("exchange_id") for ex in state.exceptions for entry in ex.audit}
        reviews = [exchange for exchange in exchanges
                   if exchange.reply.mode is ReplyMode.ESCALATE and exchange.id not in linked]
        return {
            "state": state, "now": now, "minute": minute if replay else service.minute,
            "replay": replay, "latest": latest, "last_event": last_event,
            "seed_facts": event_facts(state, last_event) if last_event else [],
            "record": record, "attention": attention, "handled": handled, "fine": fine,
            "approvals": approvals, "reviews": reviews,
            "pending_approvals": sum(row["status"] == "PENDING" and not row["stale"] for row in approvals),
            "message_id": str(uuid4()), "error": None, "notice": None, "typed_text": "",
        }
