from __future__ import annotations

from argus.config import Settings


def test_operator_list_environment_values_use_csv_without_json(monkeypatch):
    monkeypatch.setenv("ARGUS_ALLOW_INTERNAL_TARGETS", "127.0.0.1, localhost")
    monkeypatch.setenv("ARGUS_DENY_OUTBOUND_HOSTS", "Metadata.Google.Internal, example.test")
    monkeypatch.setenv("ARGUS_THROTTLED_DOMAINS", "Example.COM, example.com, slow.test")
    monkeypatch.setenv("ARGUS_OUTBOUND_PUBLIC_PORTS", "443,80,443")

    settings = Settings(_env_file=None)

    assert settings.allow_internal_targets == ["127.0.0.1", "localhost"]
    assert settings.deny_outbound_hosts == ["example.test", "metadata.google.internal"]
    assert settings.throttled_domains == ["example.com", "slow.test"]
    assert settings.outbound_public_ports == [80, 443]
