"""init_db.py must explain a collMod permission error instead of crashing.

Atlas's "Read and write to any database" role (readWriteAnyDatabase) cannot run
collMod, which is how a validator is applied to an existing collection such as
curriculum-designer's `labs`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import mongomock
from pymongo.errors import OperationFailure

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "init_db.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("init_db_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unauthorized_collmod_prints_the_fix(monkeypatch, capsys):
    init_db = _load_script()
    monkeypatch.setattr(init_db.client, "get_db", lambda: mongomock.MongoClient()["aied"])

    def denied(db, **kwargs):
        raise OperationFailure("not authorized on aied to execute command { collMod: \"labs\" }",
                               code=13, details={"errmsg": "not authorized on aied to execute command collMod"})

    monkeypatch.setattr(init_db.schema, "ensure_all", denied)
    assert init_db.apply(validation_level="moderate", with_vector=False, with_roles=False) == 1
    err = capsys.readouterr().err
    assert "dbAdmin" in err and "aied" in err and "re-run" in err


def test_other_server_errors_still_raise(monkeypatch):
    init_db = _load_script()
    monkeypatch.setattr(init_db.client, "get_db", lambda: mongomock.MongoClient()["aied"])

    def broken(db, **kwargs):
        raise OperationFailure("something else", code=2)

    monkeypatch.setattr(init_db.schema, "ensure_all", broken)
    try:
        init_db.apply(validation_level="moderate", with_vector=False, with_roles=False)
    except OperationFailure as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected the error to propagate")
