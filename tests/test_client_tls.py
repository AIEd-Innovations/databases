"""Atlas connections trust certifi's CA bundle; local servers are left alone.

Without this, python.org builds of Python on macOS fail the Atlas handshake
with CERTIFICATE_VERIFY_FAILED.
"""

from __future__ import annotations

import certifi

from aied_db.client import _tls_options

ATLAS = "mongodb+srv://u:p@cluster0.abc123.mongodb.net/?retryWrites=true&w=majority"


def test_atlas_srv_uri_uses_certifi():
    assert _tls_options(ATLAS) == {"tlsCAFile": certifi.where()}


def test_explicit_tls_uri_uses_certifi():
    assert _tls_options("mongodb://h1:27017,h2:27017/?ssl=true&replicaSet=rs0") == {"tlsCAFile": certifi.where()}
    assert _tls_options("mongodb://h1:27017/?tls=true") == {"tlsCAFile": certifi.where()}


def test_local_plain_uri_is_left_alone():
    # tlsCAFile would switch TLS on and break a local mongod
    assert _tls_options("mongodb://localhost:27017") == {}


def test_uri_with_its_own_ca_file_wins():
    assert _tls_options(ATLAS + "&tlsCAFile=/etc/ssl/my.pem") == {}
