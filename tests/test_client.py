import json
import pytest
from unittest.mock import patch, MagicMock
from notellm.client import OnbClient, OnbError, OnbAuthError, OnbNotFoundError
from notellm.ir import OnbConfig


@pytest.fixture
def client():
    config = OnbConfig(api_url="http://test:5055", password="testpw", max_retries=2, retry_backoff=0.01)
    return OnbClient(config)


def test_ensure_token(client):
    token = client._ensure_token()
    assert token == "testpw"


def test_ensure_token_empty():
    config = OnbConfig(password="")
    c = OnbClient(config)
    assert c._ensure_token() == ""


def test_cache_hit(client):
    calls = []
    def fetcher():
        calls.append(1)
        return {"data": 42}

    r1 = client.cached("k", 60, fetcher)
    r2 = client.cached("k", 60, fetcher)
    assert r1 == r2 == {"data": 42}
    assert len(calls) == 1


def test_cache_invalidate(client):
    calls = []
    def fetcher():
        calls.append(1)
        return len(calls)

    client.cached("k", 60, fetcher)
    client.invalidate_cache("k")
    r = client.cached("k", 60, fetcher)
    assert r == 2
    assert len(calls) == 2


def test_cache_invalidate_all(client):
    client.cached("a", 60, lambda: 1)
    client.cached("b", 60, lambda: 2)
    client.invalidate_cache()
    assert client._cache == {}
