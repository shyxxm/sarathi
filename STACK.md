# Sarathi — Tech Stack

Closed list. Adding anything to it is a decision, not a convenience.

---

## Core

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Everything below assumes it |
| Web | FastAPI | Async, and the same process serves both surfaces |
| Templating | Jinja2 + htmx | Server-rendered. No build step, no node, no SPA |
| ORM | SQLAlchemy 2.0 | Typed, and you already know it |
| Validation | Pydantic v2 | Structured model output binds directly to contracts |
| DB | Postgres 16 | Operational truth |
| Vectors | pgvector | Same database. One less service, one less failure mode |
| Tests | pytest | `eval/` runs in under a second |
| Env | python-dotenv | `.env.example` committed, `.env` never |

**Not React.** Two surfaces, both mostly text, both mostly server state. htmx
polling gives you live updates in about fifteen lines. A build step is the last
thing a solo project needs.

**Not Zilliz.** pgvector is enough at this scale, and keeping SOPs beside
operational data means one connection string and one backup.

---

## Models

| Layer | Choice | Why |
|---|---|---|
| Gateway | LiteLLM | One interface, swap providers without touching agents |
| Tracing | Langfuse | Self-hosted via compose. Traces every node and every pause |
| Interpretation | cheap tier | ~35 calls/shift, mostly noise |
| Responder / decision | strong tier | Only on exceptions and questions |

Route through LiteLLM from the first line. Retrofitting a gateway after you've
scattered provider SDK calls through six modules is miserable.

Tag every Langfuse trace with `trip_id` and `exception_id` so you can open a
whole shipment's reasoning as one tree.

---

## Voice — the risky part

Do this milestone with your eyes open. It is the most likely thing to eat a
week.

**The problem.** Malayalam ASR word error rates are high even on clean read
speech — a Whisper-small fine-tuned specifically for Malayalam reports a best
WER around 38% on CommonVoice. Your actual input is code-mixed
Malayalam-English, spoken one-handed, with engine noise. Assume the transcript
is wrong a meaningful fraction of the time.

**The mitigation is architectural, not model selection.** Sarathi restates what
it understood in every reply, so the driver corrects it in the next breath. Get
that loop right and a 70%-accurate transcript is workable. Get it wrong and a
95%-accurate one still feels broken.

### Speech to text

| Option | Notes |
|---|---|
| **Sarvam (Saarika)** | Indian, built for Indian-language and code-mixed audio. Start here |
| Whisper large-v3 via API | Decent Malayalam, weak on code-mixing, easy to try |
| `faster-whisper` local | Free, offline, slower; useful as the fallback path |
| Malayalam-finetuned Whisper (HF) | Better on pure Malayalam, worse on mixed |

Test all of them on **your own four voice notes** before choosing. Published
WER on read speech will not predict performance on a man shouting over a diesel
engine.

### Text to speech

| Option | Notes |
|---|---|
| **Sarvam TTS** | Advertises mid-sentence Malayalam-English switching from a natively bilingual model — exactly your register. Start here |
| AI4Bharat IndicF5 | Open, 11 Indian languages, trained on ~1400h. Check the licence before any non-learning use |
| Facebook MMS-TTS (mal) | Runs locally, robotic, CC-BY-NC. Fine as a fallback |
| Google Cloud TTS (ml-IN) | Reliable, unexciting, easy billing |

**Always keep text reply as a working path.** TTS must be a rendering step at
the end, never a dependency the rest of the system waits on. If the voice model
is down, Sarathi still answers in text and the app still works.

---

## Deferred deliberately

| Thing | When | Why deferred |
|---|---|---|
| LangGraph | M8 | Five nodes and a router is clearer as plain Python. Port it *after* it works, as an exercise — you'll learn more from feeling what the framework buys you than from starting inside it |
| FastMCP | M8 | Genuinely worth building once, and directly transferable. But your tools are local function calls until something external needs them |
| Zilliz / Milvus | never, probably | pgvector is sufficient here |
| Docker for the app | later | Compose runs Postgres and Langfuse. Run the app on the host while iterating |
| Auth | never | There are no real users |

Both deferrals are about sequencing, not dismissal. Build the thing, then
refactor it into the framework and watch what changes. That refactor is a good
blog post, which is a good reason for the repo to be public.

---

## Infrastructure

One `docker-compose.yml`: Postgres with the pgvector extension, and Langfuse.
Nothing else. The app runs on the host with `uvicorn --reload`.

Secrets in `.env`, gitignored, with `.env.example` committed listing every key
name and no values.

---

## Repo

```
sarathi/
  app/            see CLAUDE.md
  scripts/
  eval/
  docker-compose.yml
  pyproject.toml
  .env.example
  README.md
  SPEC.md  CLAUDE.md  BRIEF.md  STACK.md
  LICENSE            # MIT or Apache-2.0
```

Public from the first commit. A real commit history is worth more to a reader
than a polished initial dump, and it's the difference between a portfolio piece
and a code drop.
