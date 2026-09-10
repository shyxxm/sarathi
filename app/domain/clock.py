from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

ONE_MINUTE = timedelta(minutes=1)


@runtime_checkable
class Clock(Protocol):
    """The only way anything in domain/ learns the time. See CLAUDE.md rule 5."""

    def now(self) -> datetime: ...


class SimulatedClock:
    """Walks a shift window in fixed steps. Never reads wall time."""

    def __init__(
        self,
        start: datetime,
        end: datetime,
        step: timedelta = ONE_MINUTE,
    ) -> None:
        if end < start:
            raise ValueError("end is before start")
        if step <= timedelta(0):
            raise ValueError("step must be positive")
        self.start = start
        self.end = end
        self.step = step
        self._current = start

    def now(self) -> datetime:
        return self._current

    @property
    def exhausted(self) -> bool:
        return self._current >= self.end

    def advance(self, step: timedelta | None = None) -> datetime:
        """Move one step forward, never past the end of the window."""
        self._current = min(self._current + (step or self.step), self.end)
        return self._current

    def ticks(self) -> Iterator[datetime]:
        """Yield every instant of the window, current one first, advancing."""
        yield self._current
        while not self.exhausted:
            yield self.advance()

    def reset(self) -> None:
        self._current = self.start


class FrozenClock:
    """Stopped at a fixed instant. For tests."""

    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = at

    def advance(self, step: timedelta) -> datetime:
        self._at = self._at + step
        return self._at
