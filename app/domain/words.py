"""Everything code says to a driver, by language. SPEC 7.2.

His record of the day, the facts restated to him, read-backs, the questions
the state machine asks when it disagrees with him, check-ins, escalation and
failure wording. The dispatcher, customer drafts, the responder's context and
traces stay English and use `EVENT_WORDS` / `STATUS_PHRASE` below.

Whole sentences with named slots, never English fragments stitched together:
Malayalam word order is not English word order. A slot may be left out of a
translation; it may not be invented.

One utterance, one language. `spoken()` gives his language once its table is
complete and English until then — never half of each, which is how a driver
came to hear three scripts in one reply. `TODO` marks a line still to write.
"""

from string import Formatter

from app.contracts.enums import EventType, Language, StopStatus

TODO = "TODO"

EN: dict[str, str] = {
    # What happened. His record of the day, read-backs, check-ins.
    "event.DEPARTED": "You set off",
    "event.ARRIVED_STOP": "You arrived",
    "event.SERVICE_STARTED": "Unloading started",
    "event.STOP_COMPLETED": "Delivery completed",
    "event.GATE_CLOSED": "The gate is closed",
    "event.CONSIGNEE_ABSENT": "Nobody is there to receive the delivery",
    "event.VEHICLE_BREAKDOWN": "The vehicle has broken down",
    "event.DOCUMENT_ISSUE": "There is a problem with the paperwork",
    "event.SHORTAGE_OR_DAMAGE": "A shortage or damage was reported",
    "event.DELIVERY_REFUSED": "The delivery was refused",
    "event.ACKNOWLEDGEMENT": "Your message was received",
    "event.UNCLEAR": "Your message needs clarification",
    "event.STOP_OVERDUE": "We checked whether you need help reaching the stop",
    "event.DRIVER_SILENT": "Everything alright? Need anything?",
    "event.WINDOW_AT_RISK": "The delivery window may be missed",
    "event.DETENTION_CROSSED": "The recorded wait passed the free allowance",
    "event.REATTEMPT_SCHEDULED": "Another delivery attempt was scheduled",
    # Where a stop stands.
    "status.PENDING": "not started",
    "status.EN_ROUTE": "on the way",
    "status.ARRIVED": "arrived, unloading not started",
    "status.IN_SERVICE": "unloading",
    "status.COMPLETED": "delivered",
    "status.FAILED": "not delivered",
    "status.REATTEMPT_SCHEDULED": "waiting for a reattempt",
    # What he told us, said back inside a question. {said} in reject.disagrees.
    "said.ARRIVED_STOP": "you have reached it",
    "said.SERVICE_STARTED": "unloading has started",
    "said.STOP_COMPLETED": "the delivery is done",
    "said.DELIVERY_REFUSED": "they refused it",
    "said.DEPARTED": "you have set off",
    "said.REATTEMPT_SCHEDULED": "we are setting up another attempt",
    "said.other": "something else happened",
    # The state machine disagreeing with him, and asking.
    "label.stop": "stop {seq}, {customer}",
    "reject.shift_closed": "Today's trip is already closed off. I will pass this to the office.",
    "reject.which_stop": "I am not sure which stop that is about. Which stop are you at?",
    "reject.leaving_from": "I am not sure where you are leaving from. Which stop?",
    "reject.already": "I already have {stop} as {status}. Has something changed?",
    "reject.disagrees": "I have {stop} as {status}, and you are telling me {said}. Which is right?",
    "reject.not_on_road": "I do not have you on the road yet. Did you leave the depot?",
    "reject.wrong_stop": "I have you on the way to {here}, not {stop}. Which stop have you reached?",
    # His record, read back.
    "record.event": "{event}.",
    "record.event_at_stop": "{event} — stop {seq}, {customer}.",
    "record.arrival": "Your arrival was recorded at {time}.",
    "record.claimed_wait": "You said you had waited about {minutes}.",
    # Why a message went to a person.
    "issue.correction": "You asked to correct the record. Your earlier record is unchanged for now.",
    "issue.unclear": "I could not make out what happened or which stop this is about.",
    "failed.reached": "Your message reached me, but I could not process it. The fault is ours, not your words.",
    "failed.nothing_recorded": "Nothing has been recorded on your trip.",
    # The facts code restates with a model reply (SPEC 5.2).
    "fact.stop": "Stop {seq}, {customer}: {status}.",
    "fact.event": "{event}.",
    "fact.problem": "{event} — recorded at {time}.",
    "fact.asked": 'You asked: "{question}"',
    "fact.claimed_wait": "You said you have been waiting {minutes}.",
    "fact.waiting": "Waiting counted from {time}, when we recorded your arrival: {minutes} so far.",
    "fact.free_time": "Free time here: {minutes}.",
    "fact.past_free": "Past the free time by {minutes}.",
    "fact.free_left": "{minutes} of free time left.",
    "fact.window": "Delivery window {open}–{close}.",
    "fact.now": "It is now {time}.",
    "unit.minute_one": "{n} minute",
    "unit.minute_many": "{n} minutes",
    # Wrapping a reply that goes to a person.
    "escalation.opening": "Recorded: {facts}.",
    "escalation.closing": "Someone at the office is checking the rest now.",
    "failure.closing": "A dispatcher has your message and will get back to you.",
    "hedge": "I'm confirming this with the office.",
}

# Malayalam. Every key English has, each still to write unless filled. The
# English is beside each line; keep the {slots} you need, drop any you do not.
ML: dict[str, str] = {
    "event.DEPARTED": TODO,  # You set off
    "event.ARRIVED_STOP": TODO,  # You arrived
    "event.SERVICE_STARTED": TODO,  # Unloading started
    "event.STOP_COMPLETED": TODO,  # Delivery completed
    "event.GATE_CLOSED": TODO,  # The gate is closed
    "event.CONSIGNEE_ABSENT": TODO,  # Nobody is there to receive the delivery
    "event.VEHICLE_BREAKDOWN": TODO,  # The vehicle has broken down
    "event.DOCUMENT_ISSUE": TODO,  # There is a problem with the paperwork
    "event.SHORTAGE_OR_DAMAGE": TODO,  # A shortage or damage was reported
    "event.DELIVERY_REFUSED": TODO,  # The delivery was refused
    "event.ACKNOWLEDGEMENT": TODO,  # Your message was received
    "event.UNCLEAR": TODO,  # Your message needs clarification
    "event.STOP_OVERDUE": TODO,  # We checked whether you need help reaching the stop
    "event.DRIVER_SILENT": TODO,  # Everything alright? Need anything?
    "event.WINDOW_AT_RISK": TODO,  # The delivery window may be missed
    "event.DETENTION_CROSSED": TODO,  # The recorded wait passed the free allowance
    "event.REATTEMPT_SCHEDULED": TODO,  # Another delivery attempt was scheduled
    "status.PENDING": TODO,  # not started
    "status.EN_ROUTE": TODO,  # on the way
    "status.ARRIVED": TODO,  # arrived, unloading not started
    "status.IN_SERVICE": TODO,  # unloading
    "status.COMPLETED": TODO,  # delivered
    "status.FAILED": TODO,  # not delivered
    "status.REATTEMPT_SCHEDULED": TODO,  # waiting for a reattempt
    "said.ARRIVED_STOP": TODO,  # you have reached it
    "said.SERVICE_STARTED": TODO,  # unloading has started
    "said.STOP_COMPLETED": TODO,  # the delivery is done
    "said.DELIVERY_REFUSED": TODO,  # they refused it
    "said.DEPARTED": TODO,  # you have set off
    "said.REATTEMPT_SCHEDULED": TODO,  # we are setting up another attempt
    "said.other": TODO,  # something else happened
    "label.stop": TODO,  # stop {seq}, {customer}
    "reject.shift_closed": TODO,  # Today's trip is already closed off. I will pass this to the office.
    "reject.which_stop": TODO,  # I am not sure which stop that is about. Which stop are you at?
    "reject.leaving_from": TODO,  # I am not sure where you are leaving from. Which stop?
    "reject.already": TODO,  # I already have {stop} as {status}. Has something changed?
    "reject.disagrees": TODO,  # I have {stop} as {status}, and you are telling me {said}. Which is right?
    "reject.not_on_road": TODO,  # I do not have you on the road yet. Did you leave the depot?
    "reject.wrong_stop": TODO,  # I have you on the way to {here}, not {stop}. Which stop have you reached?
    "record.event": TODO,  # {event}.
    "record.event_at_stop": TODO,  # {event} — stop {seq}, {customer}.
    "record.arrival": TODO,  # Your arrival was recorded at {time}.
    "record.claimed_wait": TODO,  # You said you had waited about {minutes}.
    "issue.correction": TODO,  # You asked to correct the record. Your earlier record is unchanged for now.
    "issue.unclear": TODO,  # I could not make out what happened or which stop this is about.
    "failed.reached": TODO,  # Your message reached me, but I could not process it. The fault is ours, not your words.
    "failed.nothing_recorded": TODO,  # Nothing has been recorded on your trip.
    "fact.stop": TODO,  # Stop {seq}, {customer}: {status}.
    "fact.event": TODO,  # {event}.
    "fact.problem": TODO,  # {event} — recorded at {time}.
    "fact.asked": TODO,  # You asked: "{question}"   ({question} is the interpreter's English)
    "fact.claimed_wait": TODO,  # You said you have been waiting {minutes}.
    "fact.waiting": TODO,  # Waiting counted from {time}, when we recorded your arrival: {minutes} so far.
    "fact.free_time": TODO,  # Free time here: {minutes}.
    "fact.past_free": TODO,  # Past the free time by {minutes}.
    "fact.free_left": TODO,  # {minutes} of free time left.
    "fact.window": TODO,  # Delivery window {open}–{close}.
    "fact.now": TODO,  # It is now {time}.
    "unit.minute_one": TODO,  # {n} minute
    "unit.minute_many": TODO,  # {n} minutes
    # Already in use before this table existed.
    "escalation.opening": "രേഖപ്പെടുത്തി: {facts}.",
    "escalation.closing": "ബാക്കി ഓഫീസിൽ ഒരാൾ നോക്കുന്നുണ്ട്.",
    "failure.closing": TODO,  # A dispatcher has your message and will get back to you.
    "hedge": "ഇത് ഓഫീസിൽ ഉറപ്പാക്കുന്നുണ്ട്.",
}

# Only the lines that existed before this table. Neither is complete, so code's
# own utterances to these drivers are English; the hedge line still matches
# the language the responder wrote in.
MIXED: dict[str, str] = {
    "escalation.opening": "രേഖപ്പെടുത്തി: {facts}.",
    "escalation.closing": "ബാക്കി ഓഫീസിൽ ഒരാൾ check ചെയ്യുന്നുണ്ട്.",
    "hedge": "ഇത് ഓഫീസിൽ confirm ചെയ്യുന്നുണ്ട്.",
}
HI: dict[str, str] = {
    "escalation.opening": "दर्ज कर लिया: {facts}.",
    "escalation.closing": "बाकी ऑफिस में एक व्यक्ति देख रहा है.",
    "hedge": "यह ऑफिस से पक्का कर रहा हूँ.",
}

TABLES: dict[Language, dict[str, str]] = {
    Language.EN: EN, Language.ML: ML, Language.MIXED: MIXED, Language.HI: HI,
}

# English, for everything that is not said to a driver: the board, customer
# drafts, the responder's context, traces.
EVENT_WORDS: dict[EventType, str] = {event: EN[f"event.{event.value}"] for event in EventType}
STATUS_PHRASE: dict[StopStatus, str] = {status: EN[f"status.{status.value}"] for status in StopStatus}


def missing(language: Language) -> list[str]:
    """Keys English has that this language has not written yet."""
    table = TABLES.get(language, {})
    return [key for key in EN if table.get(key, TODO) == TODO]


def spoken(language: Language) -> Language:
    """The language one utterance of code's words is said in: his, once his
    table is complete, and English until then. Never half of each."""
    return language if not missing(language) else Language.EN


def say(language: Language, key: str, **slots) -> str:
    """One line in a language `spoken()` chose. A line that language has not
    written is English rather than the marker — a driver never hears "TODO"."""
    template = TABLES.get(language, {}).get(key, TODO)
    return (EN[key] if template == TODO else template).format(**slots)


def line(language: Language, key: str) -> str:
    """A single line appended to text a model already wrote in his language —
    the hedge. His language where it is written, whether or not the rest is."""
    return say(language, key)


def minutes(language: Language, count: int) -> str:
    return say(language, "unit.minute_one" if count == 1 else "unit.minute_many", n=count)


def slots(template: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(template) if field}
