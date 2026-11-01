"""The `.env` loader used by this repo's scripts.

The library never reads files — `client.py` reads `os.environ` only — so this
covers the scripts' convenience layer and the placeholder guard that catches
the most common setup mistake.
"""

from __future__ import annotations

import pytest

from aied_db._env import load_env_file, uri_problem

GOOD = "mongodb+srv://me:secret@aied.ab12cde.mongodb.net/?retryWrites=true&w=majority"


def write(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_loads_simple_pairs(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGODB_DB", raising=False)
    names = load_env_file(write(tmp_path, "MONGODB_DB=aied_test\n"))
    assert names == ["MONGODB_DB"]
    import os

    assert os.environ["MONGODB_DB"] == "aied_test"


def test_strips_quotes_comments_and_export(tmp_path, monkeypatch):
    """A real .env has all three; an Atlas URI is usually quoted for the `&`."""
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("MONGODB_DB", raising=False)
    load_env_file(
        write(
            tmp_path,
            "# a comment\n"
            "\n"
            f"export MONGODB_URI='{GOOD}'\n"
            'MONGODB_DB="aied_quoted"\n'
            "NOT_A_PAIR\n",
        )
    )
    import os

    assert os.environ["MONGODB_URI"] == GOOD
    assert os.environ["MONGODB_DB"] == "aied_quoted"


def test_an_exported_variable_wins_over_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MONGODB_DB", "from_shell")
    load_env_file(write(tmp_path, "MONGODB_DB=from_file\n"))
    import os

    assert os.environ["MONGODB_DB"] == "from_shell"


def test_override_reverses_that(tmp_path, monkeypatch):
    monkeypatch.setenv("MONGODB_DB", "from_shell")
    load_env_file(write(tmp_path, "MONGODB_DB=from_file\n"), override=True)
    import os

    assert os.environ["MONGODB_DB"] == "from_file"


def test_a_missing_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "nope.env") == []


# ── The placeholder guard ────────────────────────────────────────────────────


def test_a_real_looking_uri_passes():
    assert uri_problem(GOOD) is None


def test_empty_uri_is_explained():
    assert "empty" in uri_problem("")


def test_a_non_connection_string_is_caught():
    assert "connection string" in uri_problem("https://cloud.mongodb.com/")


@pytest.mark.parametrize(
    "uri",
    [
        "mongodb+srv://USER:PASSWORD@CLUSTER.mongodb.net/?retryWrites=true",
        "mongodb+srv://me:<db_password>@aied.ab12cde.mongodb.net/",
        "mongodb+srv://username:password@aied.ab12cde.mongodb.net/",
        "mongodb+srv://me:pw@cluster.mongodb.net/",
    ],
)
def test_unreplaced_placeholders_are_caught_before_connecting(uri):
    """Each of these otherwise fails with a DNS or auth error that misleads."""
    problem = uri_problem(uri)
    assert problem is not None
    assert "placeholder" in problem
