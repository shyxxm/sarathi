"""Langfuse traces. Best effort, never load bearing. SPEC 7.1.

Nothing in here may change what Sarathi does. Every call into the SDK is
wrapped: a Langfuse that is down, misconfigured or raising is logged at debug
and the pipeline carries on exactly as it would with tracing off. So is the
code that builds what gets recorded — callers pass it as a function, which
runs only when tracing is on and whose failure is the trace's problem.

No keys, no client. And no observation is started outside a trace, so a
calibration run calling the interpreter directly leaves nothing behind.

The session is the trip. There is no `user_id` and there must never be one:
Langfuse builds a per-user view out of it, which is a per-driver history by
another name (CLAUDE.md rule 1).
"""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import logging
import os
from threading import Lock

LOG = logging.getLogger(__name__)

_lock = Lock()
_backend = None  # (client, propagate_attributes), or False once found disabled
_in_trace: ContextVar[bool] = ContextVar("sarathi_in_trace", default=False)


def _load():
    global _backend
    with _lock:
        if _backend is None:
            _backend = _build() or False
        return _backend or None


def _build():
    try:
        from dotenv import load_dotenv

        load_dotenv()
        if os.getenv("LANGFUSE_TRACING_ENABLED", "true").lower() == "false":
            return None
        if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
            return None
        from langfuse import Langfuse, propagate_attributes

        return Langfuse(), propagate_attributes
    except Exception:
        LOG.debug("Langfuse unavailable; tracing is off", exc_info=True)
        return None


def _close(stack: ExitStack):
    try:
        stack.close()
    except Exception:
        LOG.debug("Closing a trace observation failed", exc_info=True)


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:500]


def usage(response) -> dict[str, int] | None:
    try:
        return {"input": int(response.usage.prompt_tokens),
                "output": int(response.usage.completion_tokens)}
    except Exception:
        return None


class Observation:
    """What a caller holds. Every method is safe whether tracing is on or not."""

    def __init__(self, span=None):
        self._span = span

    def record(self, build):
        """`build()` returns the fields to set. It runs only when tracing is on."""
        if self._span is None:
            return
        try:
            self._span.update(**build())
        except Exception:
            LOG.debug("Recording on a trace observation failed", exc_info=True)


class Trace(Observation):
    def __init__(self, span=None, stack=None, propagate=None, input=None):
        super().__init__(span)
        self._stack, self._propagate, self._input, self._ids = stack, propagate, input, {}

    def parent(self):
        """Carry only ids across the later audio request, never an open span."""
        if self._span is not None:
            try:
                return {"trace_context": {"trace_id": self._span.trace_id,
                                          "parent_span_id": self._span.id},
                        "ids": dict(self._ids)}
            except Exception:
                LOG.debug("Reading trace ids failed", exc_info=True)
        return None

    def tag(self, build):
        """Add ids as they are resolved: the stop and the exception are not
        known when the message arrives. Call it with no child observation open —
        the attributes land on the active span and on everything after it."""
        if self._span is None:
            return
        try:
            self._ids.update({key: str(value) for key, value in build().items() if value})
            self._stack.enter_context(self._propagate(
                session_id=self._ids.get("trip_id"),
                tags=sorted(f"{key.removesuffix('_id')}:{value}" for key, value in self._ids.items()),
                metadata=dict(self._ids),
            ))
        except Exception:
            LOG.debug("Tagging a trace failed", exc_info=True)

    def finish(self, build):
        """The trace's own output — what the driver was told — and metadata."""
        if self._span is None:
            return
        try:
            fields = build()
            self._span.update(**fields)
            self._span.set_trace_io(input=self._input, output=fields.get("output"))
        except Exception:
            LOG.debug("Finishing a trace failed", exc_info=True)


@contextmanager
def trace(name: str, *, seed: str, input=None, **ids):
    """One trace, its id derived from `seed` — the driver's message id — so the
    message someone is asking about is one lookup away."""
    backend, stack, handle = _load(), ExitStack(), Trace()
    if backend is not None:
        client, propagate = backend
        try:
            root = stack.enter_context(client.start_as_current_observation(
                trace_context={"trace_id": client.create_trace_id(seed=seed)},
                name=name, as_type="span", input=input,
            ))
            handle = Trace(root, stack, propagate, input)
            handle.tag(lambda: ids)
            stack.callback(_in_trace.reset, _in_trace.set(True))
        except Exception:
            LOG.debug("Starting a trace failed; carrying on without one", exc_info=True)
            _close(stack)
            stack, handle = ExitStack(), Trace()
    try:
        yield handle
    except BaseException as error:
        handle.record(lambda: {"level": "ERROR", "status_message": _describe(error)})
        raise
    finally:
        _close(stack)


@contextmanager
def observe(name: str, *, as_type: str = "span", parent=None, **fields):
    """Under the current trace, or a saved parent for deferred rendering."""
    backend = _load() if _in_trace.get() or parent else None
    stack, handle = ExitStack(), Observation()
    if backend is not None:
        try:
            if parent:
                fields["trace_context"] = parent["trace_context"]
                ids = parent["ids"]
                stack.enter_context(backend[1](
                    session_id=ids.get("trip_id"),
                    tags=sorted(f"{key.removesuffix('_id')}:{value}" for key, value in ids.items()),
                    metadata=ids,
                ))
            handle = Observation(stack.enter_context(backend[0].start_as_current_observation(
                name=name, as_type=as_type, **fields)))
        except Exception:
            LOG.debug("Starting a trace observation failed", exc_info=True)
            _close(stack)
            stack, handle = ExitStack(), Observation()
    try:
        yield handle
    except BaseException as error:
        handle.record(lambda: {"level": "ERROR", "status_message": _describe(error)})
        raise
    finally:
        _close(stack)


def event(name: str, build):
    """A point on the trace: code that decided something without a model."""
    with observe(name) as span:
        span.record(build)


def start():
    """Build the client at app startup rather than on the first driver
    message: the SDK import and setup cost about 300 ms, once."""
    _load()


def flush():
    """For scripts that exit straight after. The app never calls this on a
    request path: with Langfuse unreachable it would wait out the timeout."""
    backend = _load()
    if backend is not None:
        try:
            backend[0].flush()
        except Exception:
            LOG.debug("Flushing traces failed", exc_info=True)
