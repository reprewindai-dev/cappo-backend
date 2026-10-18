"""Adversarial tests for P0-4 (SSRF + endpoint/network enforcement)."""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from cappo_backend.security.ssrf import (
    EndpointClass,
    SSRFValidationError,
    is_safe_url,
    validate_endpoint,
)
from cappo_backend.services.providers import OpenAICompatExecutor


def mock_getaddrinfo(ip_list: list[str]):
    def side_effect(host, port, *args, **kwargs):
        # Return format of socket.getaddrinfo:
        # [(family, type, proto, canonname, sockaddr)]
        # sockaddr is (address, port) for IPv4 or (address, port, flowinfo, scope_id) for IPv6
        res = []
        for ip in ip_list:
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sockaddr = (ip, 80, 0, 0) if family == socket.AF_INET6 else (ip, 80)
            res.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return res
    return side_effect

def test_aws_metadata_url_is_blocked() -> None:
    # 169.254.169.254 is link-local (forbidden range)
    with patch("socket.getaddrinfo", mock_getaddrinfo(["169.254.169.254"])):
        with pytest.raises(SSRFValidationError, match="forbidden IP range"):
            validate_endpoint("http://169.254.169.254/latest/meta-data/", EndpointClass.TENANT_MANAGED_OLLAMA)
        with pytest.raises(SSRFValidationError, match="forbidden IP range"):
            validate_endpoint("https://169.254.169.254/latest/meta-data/", EndpointClass.EXTERNAL_PROVIDER)

def test_hostname_resolving_to_link_local_is_blocked() -> None:
    # Hostname resolves to link-local IP
    with patch("socket.getaddrinfo", mock_getaddrinfo(["169.254.169.254"])):
        with pytest.raises(SSRFValidationError, match="forbidden IP range"):
            validate_endpoint("https://attacker.test/v1", EndpointClass.EXTERNAL_PROVIDER)

def test_unspecified_ip_is_blocked() -> None:
    # 0.0.0.0 or :: is unspecified
    with patch("socket.getaddrinfo", mock_getaddrinfo(["0.0.0.0"])):
        with pytest.raises(SSRFValidationError, match="forbidden IP range"):
            validate_endpoint("https://0.0.0.0/v1", EndpointClass.EXTERNAL_PROVIDER)
            
    with patch("socket.getaddrinfo", mock_getaddrinfo(["::"])):
        with pytest.raises(SSRFValidationError, match="forbidden IP range"):
            validate_endpoint("https://[::]/v1", EndpointClass.EXTERNAL_PROVIDER)

def test_ipv6_link_local_is_blocked() -> None:
    # fe80::1 is link-local IPv6
    with patch("socket.getaddrinfo", mock_getaddrinfo(["fe80::1"])):
        with pytest.raises(SSRFValidationError, match="forbidden IP range"):
            validate_endpoint("https://[fe80::1]/v1", EndpointClass.EXTERNAL_PROVIDER)

def test_loopback_and_cgnat_blocked_for_external() -> None:
    # Loopback IP
    with patch("socket.getaddrinfo", mock_getaddrinfo(["127.0.0.1"])):
        # Allowed for local/tenant classes, but localhost is not pinned
        pinned, host = validate_endpoint("http://localhost:11434", EndpointClass.VEKLOM_MANAGED_LOCAL_OLLAMA)
        assert pinned == "http://localhost:11434"
        assert host is None
        
        # Loopback resolves on a custom hostname should trigger IP pinning
        pinned_custom, host_custom = validate_endpoint("http://my-ollama.test:11434", EndpointClass.VEKLOM_MANAGED_LOCAL_OLLAMA)
        assert "127.0.0.1" in pinned_custom
        assert host_custom == "my-ollama.test"
        
        # Blocked for external class
        with pytest.raises(SSRFValidationError, match="private or loopback IP"):
            validate_endpoint("https://localhost:11434", EndpointClass.EXTERNAL_PROVIDER)

    # CGNAT range (100.64.0.1)
    with patch("socket.getaddrinfo", mock_getaddrinfo(["100.64.0.1"])):
        with pytest.raises(SSRFValidationError, match="forbidden IP range"):
            validate_endpoint("http://100.64.0.1:11434", EndpointClass.VEKLOM_MANAGED_LOCAL_OLLAMA)

def test_http_redirects_are_disabled() -> None:
    # Verify follow_redirects is set to False in OpenAICompatExecutor client
    ex = OpenAICompatExecutor("openai", "https://api.openai.com/v1", "gpt-4")
    client = ex._http()
    # httpx.Client stores follow_redirects as client.options (or config/follow_redirects depending on version)
    # We can inspect the property or perform a redirect test.
    assert client.follow_redirects is False

def test_legacy_wrapper_is_safe_url() -> None:
    with patch("socket.getaddrinfo", mock_getaddrinfo(["169.254.169.254"])):
        assert is_safe_url("http://169.254.169.254") is False
        
    with patch("socket.getaddrinfo", mock_getaddrinfo(["104.18.7.12"])):
        assert is_safe_url("https://api.openai.com/v1") is True
        # http is not allowed for external unless allow_private=True
        assert is_safe_url("http://api.openai.com/v1") is False
