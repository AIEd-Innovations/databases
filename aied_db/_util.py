"""Small helpers shared by the stores, repos and schema modules.

Timestamp policy
----------------
MongoDB stores BSON dates; `ui-student`'s SQLite stores returned ISO-8601
strings and its agents parse them with `datetime.fromisoformat`. The Mongo
stores therefore write `datetime` and read back ISO strings ending in `Z`,
so the agents cannot tell the difference. `to_dt`/`to_iso` are the only
two places that conversion happens.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from pymongo.errors import DuplicateKeyError


class IntegrityError(Exception):
    """Raised when a write violates a unique index.

    The NAME matters: `ui-student`'s GuardianAgent checks
    `"IntegrityError" in type(e).__name__` to treat a duplicate session as
    one that already exists. Renaming this class silently breaks that.
    """


def _to_millis(moment: datetime) -> datetime:
    """Drop sub-millisecond precision, which BSON cannot hold."""
    return moment.replace(microsecond=(moment.microsecond // 1000) * 1000)


def now() -> datetime:
    """Current time, timezone-aware UTC, truncated to milliseconds.

    BSON dates hold MILLISECONDS; Python datetimes hold microseconds. Without
    this truncation a timestamp changes the moment it round-trips through the
    database, so a value just written and a value read back compare unequal —
    which shows up as flaky equality checks rather than as an obvious bug.
    Truncating here means the in-memory value is always exactly what Mongo
    will store.
    """
    return _to_millis(datetime.now(timezone.utc))


def new_id() -> str:
    """A fresh UUID4 string, matching the id format the agents already use."""
    return str(uuid.uuid4())


def to_dt(value: Any) -> Optional[datetime]:
    """Coerce an incoming timestamp to a timezone-aware UTC `datetime`.

    Accepts a `datetime` (naive is assumed UTC), an ISO-8601 string with or
    without a trailing `Z`, or None.

    The result is truncated to milliseconds for the same reason `now()` is:
    BSON holds no more, so an untruncated value changes the moment it
    round-trips and a just-written timestamp no longer equals the one read
    back.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return _to_millis(moment)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return _to_millis(parsed)
    raise TypeError(f"cannot read {type(value).__name__} as a timestamp")


def to_iso(value: Any) -> Optional[str]:
    """Render a stored timestamp as an ISO-8601 string ending in `Z`.

    This is what the stores hand back, because that is what the SQLite
    stores handed back.
    """
    moment = to_dt(value)
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def strip_id(doc: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """Drop Mongo's `_id` so returned dicts match the old SQLite row shape."""
    if doc is None:
        return None
    return {k: v for k, v in doc.items() if k != "_id"}


def wrap_duplicate(exc: DuplicateKeyError) -> IntegrityError:
    """Translate a driver duplicate-key error into `IntegrityError`."""
    return IntegrityError(str(exc))
