from notellm.ir import OnbConfig, Context


def test_config_defaults():
    c = OnbConfig()
    assert c.api_url == "http://127.0.0.1:5055"
    assert c.password == ""
    assert c.max_retries == 3
    assert c.retry_backoff == 1.0
    assert c.cache_ttl == 30
    assert c.podcast_profiles == ("tech_discussion", "tech_experts")


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("NOTELLM_ONB_API", "http://localhost:9999")
    monkeypatch.setenv("NOTELLM_ONB_PASSWORD", "secret")
    monkeypatch.setenv("NOTELLM_MAX_RETRIES", "5")
    c = OnbConfig.from_env()
    assert c.api_url == "http://localhost:9999"
    assert c.password == "secret"
    assert c.max_retries == 5


def test_context_holds_references():
    c = OnbConfig()
    from notellm.client import OnbClient
    client = OnbClient(c)
    ctx = Context(client=client, config=c)
    assert ctx.config is c
    assert ctx.client is client
