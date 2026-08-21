# Security notes

The implementation follows OWASP ASVS-oriented controls appropriate to this internal service: authentication, least exposure, SSRF validation, resource limits, safe error boundaries and secret separation.

Threats intentionally not solved by ARGUS include CAPTCHA bypass, authenticated access-control bypass, paid proxy rotation and commercial anti-bot evasion.

Before exposing ARGUS beyond localhost, add reverse-proxy/TLS controls, network egress rules, secret rotation, process sandboxing, browser OS isolation and deployment-level resource quotas.
