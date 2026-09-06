"""An explicit missing provider credential must not select another account."""

import pytest

from amplifier_runtime.kernel.config import expand_env_placeholders


def test_unset_explicit_provider_secret_fails_before_mutation(monkeypatch):
    monkeypatch.delenv("RSI_MISSING_PROVIDER_KEY", raising=False)
    config = {"providers": [{"config": {"api_key": "${RSI_MISSING_PROVIDER_KEY}"}}]}
    with pytest.raises(ValueError, match="RSI_MISSING_PROVIDER_KEY"):
        expand_env_placeholders(config)
    assert config["providers"][0]["config"]["api_key"] == "${RSI_MISSING_PROVIDER_KEY}"


def test_empty_explicit_secret_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("RSI_EMPTY_PROVIDER_KEY", "")
    with pytest.raises(ValueError, match="RSI_EMPTY_PROVIDER_KEY"):
        expand_env_placeholders(
            {
                "providers": [
                    {"config": {"headers": {"authorization": "Bearer ${RSI_EMPTY_PROVIDER_KEY}"}}}
                ]
            }
        )


def test_optional_endpoint_and_explicit_secret_default_keep_existing_behavior(monkeypatch):
    monkeypatch.delenv("RSI_OPTIONAL_ENDPOINT", raising=False)
    monkeypatch.delenv("RSI_DEFAULT_KEY", raising=False)
    config = {
        "providers": [
            {
                "config": {
                    "api_key": "${RSI_DEFAULT_KEY:fixture}",
                    "base_url": "${RSI_OPTIONAL_ENDPOINT}",
                }
            }
        ]
    }
    assert expand_env_placeholders(config)["providers"][0]["config"] == {"api_key": "fixture"}
