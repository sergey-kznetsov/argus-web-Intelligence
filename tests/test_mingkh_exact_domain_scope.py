from argus.sources.mingkh_residential import MingkhResidentialAdapter


def test_mingkh_factual_source_uses_exact_host_only():
    assert MingkhResidentialAdapter._is_domain_url("https://dom.mingkh.ru/") is True
    assert MingkhResidentialAdapter._is_domain_url("https://dom.mingkh.ru/izhevsk/123456") is True

    assert MingkhResidentialAdapter._is_domain_url("https://foo.dom.mingkh.ru/izhevsk/123456") is False
    assert MingkhResidentialAdapter._is_domain_url("https://dom.mingkh.ru.evil.example/123456") is False
    assert MingkhResidentialAdapter._is_domain_url("http://evil.example/dom.mingkh.ru/123456") is False
