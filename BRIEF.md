# Sarathi — Project Brief

**Track 02 · Assistive, Accessible & Inclusive Tech**

> A driver on the road can't fill a form. The software was written for people
> sitting at desks. Sarathi meets him in his own language, by voice, and speaks
> back.

---

## The problem

A truck driver in Kerala pulls up at a warehouse and the gate is shut. Security
tells him to wait outside. He now has three problems, and only one of them is
the gate.

He has no usable way to *report* it. The dispatch software has dropdowns and
English labels and expects both hands and full attention — he has neither. So
he calls the office and waits on hold, or he sends a message into a WhatsApp
group with sixty other messages in it, or he says nothing at all.

He has no way to *find out* what to do. Can he leave and come back? Is his
waiting time being counted? Is there a site contact he should be calling? That
knowledge exists — in a customer agreement in a folder in an office he isn't in.

And he has no way to *prove* what happened. When the day runs late, the delay
lands on him. Nobody logged that the gate was shut from 10:42 to 11:27.

The common thread is that every system in his working life expects him to adapt
to it. He is the person in the chain with the least time, the least access, and
the least power, and he is the one asked to do the translating.

## What Sarathi does

He speaks. In Malayalam, in Hindi, in whatever mix he actually uses, in a voice
note, one-handed, engine running. No app to learn, no form, no dropdown.

Sarathi understands it, works out which trip and which stop he's on, and
**answers him** — in his language, by voice.

*"Recorded. You reached at 10:42. Your waiting time is being counted from then.
This customer allows thirty minutes free — after that it's billable to them, not
to you. The site contact is Rajesh, and I've messaged the office."*

He can ask it things. What does this gate need from me. Can I leave and come
back. What's the rule if the consignee isn't here. Am I going to be blamed for
this. The answers come from that customer's actual standing instructions,
retrieved and read back in plain language — never from a model's general
impression of how logistics works.

When Sarathi doesn't know, it says so and fetches a human, and it tells him
that's happening. Silence is the thing he already gets from every other system.

## The other half

Sarathi also notices what he *hasn't* said.

A driver should have reached his fourth stop an hour ago. Nothing has come
through since 13:02. No arrival, no problem report, no message at all — so
nothing has been triggered anywhere.

Sarathi reaches out. *Everything alright? Do you need anything?*

The same detection, in a dispatcher-first system, would be a flag on a
performance dashboard. Here it's a check-in. The code is identical; the
direction it points is the entire design.

## Who it's for

**Primarily the driver.** Someone whose working language isn't the software's
language, whose hands are on a wheel, and whose account of his own day currently
depends on somebody else writing it down.

**Secondarily the dispatcher**, who gets a board answering one question — what
needs me — instead of sixty unread messages and a background fear of having
missed something.

## Design commitments

An agent that listens to drivers all day is one configuration change away from
being a surveillance tool. Sarathi isn't one, by construction:

- **No driver scoring, ranking, or performance history.** Exceptions attach to
  stops and trips, never to a person.
- **No location tracking.**
- **Silence produces a check-in, not a flag.** Never "driver unresponsive."
- **Records capture what happened, not who failed.** The default use of a
  timestamped gate closure is to explain a delay, not to attribute one.
- **The driver is told what has been recorded about him**, in his own language,
  as it happens.
- **Nothing goes to a customer without human approval**, and no decision the
  system made unsupervised is ever reused to justify the next one.

## How it works

The operational world is a deterministic state machine — trips, stops, delivery
windows, detention clocks. Plain code. A language model never decides which
truck it's looking at, when a clock started, or what a delay costs.

Models do judgment, at three points: understanding what a driver said,
reasoning about what should happen next, and checking its own work before
anything reaches a human.

That last one matters. Every response is scored before it goes anywhere, and the
score is computed rather than self-reported — did identity resolution actually
succeed, did the structured output parse, how close was the retrieved policy,
and the model's own rating as one capped input among four. Below threshold, or
anything touching outbound communication, routes to a person with the full
context attached. The model cannot authorise itself.

Everything Sarathi tells a driver about policy traces to a retrieved chunk of
that specific customer's instructions. If it can't cite one, it says it doesn't
know and finds someone who does.

Postgres holds operational truth. pgvector holds the standing instructions.
Voice in and voice out. Every step traced, including every pause and the reason
for it.

WhatsApp and the transport management system are simulated interfaces in the
prototype. The intelligence layer and the operational state management are the
product.

## What a demo shows

A voice note in Malayalam, spoken over engine noise. Sarathi replying in
Malayalam — recorded, clock started, here's your protection, here's the rule.

The driver asking whether he's allowed to leave, and getting an answer grounded
in that customer's actual agreement.

A question the system isn't confident about, escalating to a dispatcher with the
score breakdown visible — and the driver being told a human is on it.

Then a stop going quietly overdue with no message from anyone, and Sarathi
reaching out to ask if everything's alright.

---

*Sarathi — the charioteer. The one who guides, not the one who watches.*
