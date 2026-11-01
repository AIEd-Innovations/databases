"""The connection: one client per process, configured from the environment."""

from __future__ import annotations

import pytest

from aied_db import client


@pytest.fixture(autouse=True)
def reset_client(monkeypatch):
    """Each test starts with no cached client and a clean environment."""
    client.close()
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGODB_DB", raising=False)
    yield
    client.close()


def test_missing_uri_fails_with_an_actionable_message():
    with pytest.raises(client.ConnectionError_) as excinfo:
        client.get_client()
    message = str(excinfo.value)
    assert "MONGODB_URI" in message
    assert ".env" in message


def test_blank_uri_is_treated_as_missing(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "   ")
    with pytest.raises(client.ConnectionError_):
        client.get_client()


def test_database_name_defaults_to_aied():
    assert client.db_name() == "aied"


def test_database_name_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("MONGODB_DB", "aied_staging")
    assert client.db_name() == "aied_staging"


def test_blank_database_name_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("MONGODB_DB", "")
    assert client.db_name() == "aied"


def test_the_client_is_created_once_and_reused(monkeypatch):
    """One pool for the process — never one per request."""
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017/?connectTimeoutMS=1")
    assert client.get_client() is client.get_client()


def test_get_db_uses_the_shared_client(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017/?connectTimeoutMS=1")
    database = client.get_db()
    assert database.name == "aied"
    assert database.client is client.get_client()


def test_get_db_accepts_an_explicit_name(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017/?connectTimeoutMS=1")
    assert client.get_db("other").name == "other"


def test_the_client_is_timezone_aware(monkeypatch):
    """`_util.to_iso` relies on reads coming back as aware UTC datetimes."""
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017/?connectTimeoutMS=1")
    assert client.get_client().codec_options.tz_aware is True


def test_close_is_idempotent():
    client.close()
    client.close()


def test_close_drops_the_cached_client(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017/?connectTimeoutMS=1")
    first = client.get_client()
    client.close()
    assert client.get_client() is not first


def test_ping_reports_an_unreachable_server(monkeypatch):
    """A bad URI must fail loudly at startup, not on the first request."""
    monkeypatch.setenv(
        "MONGODB_URI", "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=50&connectTimeoutMS=50"
    )
    with pytest.raises(client.ConnectionError_, match="cannot reach MongoDB"):
        client.ping()
