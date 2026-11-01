"""The one MongoDB connection for the whole process.

A single `MongoClient` is created on first use and reused for the life of
the process; `MongoClient` is itself a connection pool and is thread-safe,
so creating one per request would be both slower and a leak. Services call
`get_db()` once at startup, not inside a route handler.

Configuration comes from the environment, never from code:

    MONGODB_URI   required, the Atlas SRV string (keep it in .env)
    MONGODB_DB    optional, defaults to "aied"

`close()` is registered for SIGINT/SIGTERM and at interpreter exit so the
pool is drained on shutdown.
"""

from __future__ import annotations

import atexit
import os
import signal
import threading
from typing import Optional

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

DEFAULT_DB_NAME = "aied"

_client: Optional[MongoClient] = None
_lock = threading.Lock()
_handlers_installed = False


class ConnectionError_(RuntimeError):
    """Raised when the database is not reachable or not configured."""


def _uri() -> str:
    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri:
        raise ConnectionError_(
            "MONGODB_URI is not set. Put the Atlas connection string in .env "
            "(which is gitignored) — it is never hardcoded in this package."
        )
    return uri


def db_name() -> str:
    return os.environ.get("MONGODB_DB", "").strip() or DEFAULT_DB_NAME


def _tls_options(uri: str) -> dict:
    """Trust certifi's CA bundle for TLS connections (every Atlas URI).

    python.org builds of Python on macOS ship without system root
    certificates until "Install Certificates.command" is run, so the Atlas
    handshake fails with CERTIFICATE_VERIFY_FAILED. MongoDB's docs recommend
    pointing the driver at certifi instead, which works the same everywhere.
    A plain mongodb:// URI without tls=true (a local server) is left alone,
    and an explicit tlsCAFile in the URI always wins.
    """
    lowered = uri.lower()
    uses_tls = lowered.startswith("mongodb+srv://") or "tls=true" in lowered or "ssl=true" in lowered
    if not uses_tls or "tlscafile=" in lowered:
        return {}
    try:
        import certifi
    except ImportError:  # still works on machines whose Python has root certs
        return {}
    return {"tlsCAFile": certifi.where()}


def get_client() -> MongoClient:
    """Return the process-wide client, creating it on first call.

    `tz_aware=True` so every date read back is an aware UTC datetime, which
    is what `_util.to_iso` expects.
    """
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:
            uri = _uri()
            _client = MongoClient(
                uri,
                tz_aware=True,
                appname="aied_db",
                serverSelectionTimeoutMS=10_000,
                retryWrites=True,
                **_tls_options(uri),
            )
            _install_shutdown_handlers()
    return _client


def get_db(name: Optional[str] = None) -> Database:
    """Return the `aied` database (or `name`), on the shared client.

    Services open this once at startup and wrap it:
    `guarded(get_db(), Role.ASSESSMENT_AGENT, actor_id)`.
    """
    return get_client()[name or db_name()]


def ping(name: Optional[str] = None) -> bool:
    """Round-trip the server. Raises `ConnectionError_` with the cause.

    Call this at startup, before serving traffic, so a bad URI fails loudly
    at boot instead of on the first request.
    """
    try:
        get_client().admin.command("ping")
    except PyMongoError as exc:
        raise ConnectionError_(f"cannot reach MongoDB: {exc}") from exc
    return True


def close() -> None:
    """Drain the pool. Idempotent; safe to call when never connected."""
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None


def _install_shutdown_handlers() -> None:
    """Close the pool on exit and on SIGINT/SIGTERM, chaining any existing handler."""
    global _handlers_installed
    if _handlers_installed:
        return
    _handlers_installed = True
    atexit.register(close)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous = signal.getsignal(sig)
        except (ValueError, OSError):  # not the main thread
            continue

        def _handler(signum, frame, _previous=previous):
            close()
            if callable(_previous):
                _previous(signum, frame)
            elif _previous == signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                signal.raise_signal(signum)

        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):  # not the main thread
            continue
