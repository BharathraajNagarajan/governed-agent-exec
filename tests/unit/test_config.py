from gax.config import Settings, get_settings


def clear_env(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_defaults(monkeypatch):
    clear_env(monkeypatch)
    s = get_settings(env_file=None)
    assert s.anthropic_model == "claude-sonnet-5"
    assert s.mongo_uri == "mongodb://localhost:27017/?directConnection=true"
    assert s.gax_db == "gax"
    assert s.temporal_address == "localhost:7233"
    assert s.voyage_base_url == "https://ai.mongodb.com/v1"
    assert s.local_broker_signing_key.get_secret_value() == ""


def test_env_overrides_and_empty_values_fall_back(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_MODEL", "")
    monkeypatch.setenv("GAX_DB", "gax_test")
    assert get_settings(env_file=None).anthropic_model == "claude-sonnet-5"
    assert get_settings(env_file=None).gax_db == "gax_test"


def test_secrets_masked(monkeypatch):
    clear_env(monkeypatch)
    for name in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "LOCAL_BROKER_SIGNING_KEY"):
        monkeypatch.setenv(name, f"secret-value-{name}")
    s = get_settings(env_file=None)
    assert "secret-value" not in repr(s)
    assert "secret-value" not in str(s)
    assert "secret-value" not in s.model_dump_json()
    assert s.anthropic_api_key.get_secret_value() == "secret-value-ANTHROPIC_API_KEY"
