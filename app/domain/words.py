"""Everything code says to a driver, by language. SPEC 7.2.

His record of the day, the facts restated to him, read-backs, the questions
the state machine asks when it disagrees with him, check-ins, escalation and
failure wording. The dispatcher, customer drafts, the responder's context and
traces stay English and use `EVENT_WORDS` / `STATUS_PHRASE` below.

Whole sentences with named slots, never English fragments stitched together:
Malayalam word order is not English word order. A slot may be left out of a
translation; it may not be invented.

One sentence, one language. `say()` gives a sentence in his language when it,
and every line composed into it, is written — and the whole sentence in English
when any of it is not. Sentences side by side may differ; a sentence never
mixes, which is how a driver came to hear three scripts in one reply. `TODO`
marks a line still to write, and a line is heard the moment it is written.
"""

from dataclasses import dataclass, field
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
    "record.departed_to": "You set off for stop {seq}, {customer}.",
    "record.departed_from": "You left stop {seq}, {customer}.",
    "record.check_in": "Everything alright? Need anything?",
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
# A line is heard as soon as it is written. A sentence that carries another
# line — a status, an event, the minutes — waits until that one is written too.
#
# PLACEHOLDER — the lines marked `# placeholder` below, and listed in
# UNREVIEWED, were written without a Malayalam speaker. Grammar is likely
# sound; register is not verified. Replace them after review, and do not take
# them for checked text in the meantime.
#
# The English loanwords in Malayalam script are deliberate: ഫ്രീ ടൈം, ഡെലിവറി,
# സ്റ്റോപ്പ്, മിനിറ്റ്, വിൻഡോ. They are what drivers actually say for freight
# vocabulary. Do not "correct" them to native words — a model once rendered
# "free time" as സ്വതന്ത്ര സമയം, which is free as in liberty, and wrong.
ML: dict[str, str] = {
    "event.DEPARTED": "നിങ്ങൾ പുറപ്പെട്ടു",  # placeholder — You set off
    "event.ARRIVED_STOP": "നിങ്ങൾ എത്തി",  # placeholder — You arrived
    "event.SERVICE_STARTED": "ഇറക്കൽ തുടങ്ങി",  # placeholder — Unloading started
    "event.STOP_COMPLETED": "ഡെലിവറി കഴിഞ്ഞു",  # placeholder — Delivery completed
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
    "status.ARRIVED": "എത്തി, ഇറക്കൽ തുടങ്ങിയിട്ടില്ല",  # placeholder — arrived, unloading not started
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
    "record.event_at_stop": "{event} — സ്റ്റോപ്പ് {seq}, {customer}.",  # placeholder — {event} — stop {seq}, {customer}.
    "record.departed_to": "നിങ്ങൾ പുറപ്പെട്ടു — അടുത്തത് സ്റ്റോപ്പ് {seq}, {customer}.",  # placeholder — You set off for stop {seq}, {customer}.
    "record.departed_from": "നിങ്ങൾ സ്റ്റോപ്പ് {seq} വിട്ടു — {customer}.",  # placeholder — You left stop {seq}, {customer}.
    # A question on its own; no full stop after it.
    "record.check_in": TODO,  # Everything alright? Need anything?
    "record.arrival": "നിങ്ങൾ എത്തിയത് {time}-ന് രേഖപ്പെടുത്തി.",  # placeholder — Your arrival was recorded at {time}.
    "record.claimed_wait": TODO,  # You said you had waited about {minutes}.
    "issue.correction": TODO,  # You asked to correct the record. Your earlier record is unchanged for now.
    "issue.unclear": "നിങ്ങൾ പറഞ്ഞത് എനിക്ക് വ്യക്തമായില്ല",  # placeholder — I could not make out what happened or which stop this is about.
    "failed.reached": TODO,  # Your message reached me, but I could not process it. The fault is ours, not your words.
    "failed.nothing_recorded": TODO,  # Nothing has been recorded on your trip.
    "fact.stop": "സ്റ്റോപ്പ് {seq}, {customer}: {status}.",  # placeholder — Stop {seq}, {customer}: {status}.
    "fact.event": TODO,  # {event}.
    "fact.problem": TODO,  # {event} — recorded at {time}.
    # No {question} in this one: it carries the interpreter's English, and an
    # English clause inside a Malayalam sentence is the mixing problem. Say that
    # he asked; do not quote him back.
    "fact.asked": TODO,  # You asked a question.
    "fact.claimed_wait": TODO,  # You said you have been waiting {minutes}.
    "fact.waiting": "കാത്തിരിപ്പ് {time} മുതൽ കണക്കാക്കുന്നു — ഇതുവരെ {minutes}.",  # placeholder — Waiting counted from {time}, when we recorded your arrival: {minutes} so far.
    "fact.free_time": "ഇവിടെ ഫ്രീ ടൈം: {minutes}.",  # placeholder — Free time here: {minutes}.
    "fact.past_free": TODO,  # Past the free time by {minutes}.
    "fact.free_left": "ഫ്രീ ടൈം ഇനി {minutes} ബാക്കി.",  # placeholder — {minutes} of free time left.
    "fact.window": "ഡെലിവറി വിൻഡോ {open}–{close}.",  # placeholder — Delivery window {open}–{close}.
    "fact.now": "ഇപ്പോൾ സമയം {time}.",  # placeholder — It is now {time}.
    "unit.minute_one": "{n} മിനിറ്റ്",  # placeholder — {n} minute
    "unit.minute_many": "{n} മിനിറ്റ്",  # placeholder — {n} minutes
    # Already in use before this table existed.
    "escalation.opening": "രേഖപ്പെടുത്തി: {facts}.",
    "escalation.closing": "ബാക്കി ഓഫീസിൽ ഒരാൾ നോക്കുന്നുണ്ട്.",
    "failure.closing": TODO,  # A dispatcher has your message and will get back to you.
    "hedge": "ഇത് ഓഫീസിൽ ഉറപ്പാക്കുന്നുണ്ട്.",
}

# The Malayalam lines above written without a Malayalam speaker. Remove a key
# once a speaker has checked its line; the suite keeps this list honest.
UNREVIEWED: frozenset[str] = frozenset({
    "record.event_at_stop", "event.DEPARTED", "event.ARRIVED_STOP", "record.arrival",
    "event.SERVICE_STARTED", "event.STOP_COMPLETED", "unit.minute_many", "fact.stop",
    "status.ARRIVED", "fact.waiting", "fact.free_time", "fact.free_left", "fact.window",
    "fact.now", "issue.unclear",
    "record.departed_to", "record.departed_from", "unit.minute_one",
})

# Only the lines that existed before this table. Everything else is said to
# these drivers in English, a sentence at a time.
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


def written(language: Language, key: str) -> bool:
    return language is Language.EN or TABLES.get(language, {}).get(key, TODO) != TODO


@dataclass(frozen=True)
class Part:
    """A line said inside another — the status in "Stop 3: arrived", the
    minutes in "61 minutes so far". It is said in the language of the sentence
    around it, and that sentence is only his language if this line is too."""

    key: str
    slots: dict = field(default_factory=dict)


def part(key: str, **slots) -> Part:
    return Part(key, slots)


def minutes(count: int) -> Part:
    return part("unit.minute_one" if count == 1 else "unit.minute_many", n=count)


def can(language: Language, key: str, slots: dict) -> bool:
    """Whether this sentence, and every line composed into it, is written."""
    return written(language, key) and all(
        can(language, value.key, value.slots) for value in slots.values() if isinstance(value, Part))


def _render(language: Language, key: str, slots: dict) -> str:
    values = {name: _render(language, value.key, value.slots) if isinstance(value, Part) else value
              for name, value in slots.items()}
    return TABLES[language][key].format(**values)


class Said(str):
    """A sentence as said, remembering what it was said from — so a sentence
    that carries it, and must be one language, can say it again in that one."""

    def __new__(cls, text: str, language: Language, key: str | None = None, slots: dict | None = None):
        said = super().__new__(cls, text)
        said.language, said.key, said.slots = language, key, dict(slots or {})
        return said

    def can(self, language: Language) -> bool:
        return can(language, self.key, self.slots) if self.key else language is Language.EN

    def again(self, language: Language) -> "Said":
        return say(language, self.key, **self.slots) if self.key else self


def say(language: Language, key: str, **slots) -> Said:
    """One sentence: in his language if it and every line composed into it are
    written, otherwise the whole sentence in English. Never half of each, and
    never the marker — a driver does not hear "TODO"."""
    chosen = language if can(language, key, slots) else Language.EN
    return Said(_render(chosen, key, slots), chosen, key, slots)


def language_of(lines) -> Language:
    """What a reply is labelled: the one language all its lines share, else English."""
    languages = {getattr(line, "language", Language.EN) for line in lines}
    return languages.pop() if len(languages) == 1 else Language.EN


def slots(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}
