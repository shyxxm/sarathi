"""Thresholds the deterministic core runs on. SPEC 4.3.

Trip-wide, not per driver. There is no per-driver tuning here and there will
not be: a threshold that varies by person is a performance record wearing a
different hat. See CLAUDE.md rule 1.
"""

from datetime import timedelta

OVERDUE_GRACE = timedelta(minutes=15)
SILENCE_AFTER = timedelta(minutes=90)
