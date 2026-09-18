"""The separate packaging service is opt-in and never prints its credential."""
from uuid import uuid4

from pydantic import ValidationError
import pytest

from app.core.config import Settings


def test_packaging_defaults_are_disabled_and_bounded(monkeypatch):
    for name in ("PACKAGING_ENABLED", "PACKAGING_SERVICE_TOKEN", "PACKAGING_BASE_URL", "PACKAGING_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.packaging_enabled is False and settings.packaging_service_token is None
    assert settings.packaging_base_url == "http://localhost:8081" and settings.packaging_timeout_seconds == 3630


@pytest.mark.parametrize("token", [None, "", "short", "x" * 257, "x" * 31 + " ", "x" * 31 + "\n", "x" * 31 + "é"])
def test_enabled_packaging_requires_its_own_valid_credential(token):
    with pytest.raises(ValidationError): Settings(_env_file=None, packaging_enabled=True, packaging_service_token=token)


def test_packaging_credential_is_redacted_and_does_not_enable_source_mutation(monkeypatch):
    for name in ("SOURCE_MUTATIONS_ENABLED", "WORKER_MUTATION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    token = uuid4().hex
    settings = Settings(_env_file=None, packaging_enabled=True, packaging_service_token=token)
    assert token not in repr(settings) and token not in settings.model_dump_json()
    assert settings.packaging_service_token.get_secret_value() == token
    assert settings.source_mutations_enabled is False and settings.worker_mutation_token is None


@pytest.mark.parametrize("url", ["file:///local", "http://", "http://user@host", "http://user:private@host", "http://host/path",
    "http://host?query", "http://host#fragment", "http://host:0", "http://host:65536", "http://host:notaport",
    "http://host\\suffix", "http://host name", "http://host\n", "http://[invalid"])
def test_packaging_requires_a_valid_service_origin_even_while_disabled(url):
    with pytest.raises(ValidationError): Settings(_env_file=None, packaging_base_url=url)


@pytest.mark.parametrize("value", [0, -1, 3661, float("inf"), float("nan")])
def test_packaging_timeout_is_bounded(value):
    with pytest.raises(ValidationError): Settings(_env_file=None, packaging_timeout_seconds=value)


def test_packaging_config_can_target_a_private_service_origin():
    settings = Settings(_env_file=None, packaging_base_url="http://packaging:8081/", packaging_timeout_seconds=1805)
    assert settings.packaging_base_url == "http://packaging:8081/" and settings.packaging_timeout_seconds == 1805
