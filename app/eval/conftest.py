"""The suite never talks to a network, and that includes Langfuse."""

from contextlib import contextmanager, nullcontext
import os
from types import SimpleNamespace

import pytest

os.environ["LANGFUSE_TRACING_ENABLED"] = "false"


class RecordingLangfuse:
    """Just enough of the SDK to see the tree the service builds."""

    def __init__(self):
        self.observations, self.attributes, self._open = [], [], []

    def create_trace_id(self, *, seed=None):
        return f"trace-{seed}"

    @contextmanager
    def start_as_current_observation(self, *, name, trace_context=None, **fields):
        node = SimpleNamespace(name=name, parent=self._open[-1].name if self._open else None,
                               trace_context=trace_context, fields=dict(fields), trace_io={})
        node.update = lambda **more: node.fields.update(more)
        node.set_trace_io = lambda **io: node.trace_io.update(io)
        self.observations.append(node)
        self._open.append(node)
        try:
            yield node
        finally:
            self._open.pop()

    def propagate(self, **attributes):
        self.attributes.append(attributes)
        return nullcontext()

    def named(self, name):
        return [node for node in self.observations if node.name == name][-1]


def _raise(*args, **kwargs):
    raise RuntimeError("langfuse is down")


class ExplodingLangfuse:
    """Nothing works: every call into the client raises."""

    create_trace_id = start_as_current_observation = propagate = flush = staticmethod(_raise)


class BrittleLangfuse(RecordingLangfuse):
    """Observations open, then everything written to them raises."""

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        with super().start_as_current_observation(**kwargs) as node:
            node.update = node.set_trace_io = _raise
            yield node

    propagate = staticmethod(_raise)


@pytest.fixture
def langfuse(monkeypatch):
    from app import tracing

    recorder = RecordingLangfuse()
    monkeypatch.setattr(tracing, "_load", lambda: (recorder, recorder.propagate))
    return recorder


@pytest.fixture
def broken_langfuse():
    return {"exploding": ExplodingLangfuse(), "brittle": BrittleLangfuse()}
