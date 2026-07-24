"""Shared constants and helpers for agent-only network isolation."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit


NETWORK_STATE_DIR = "/var/lib/codemem-private/network"
INFERENCE_RELAY_HOST = "127.0.0.1"
INFERENCE_RELAY_PORT = 18080
INFERENCE_RELAY_ORIGIN = f"http://{INFERENCE_RELAY_HOST}:{INFERENCE_RELAY_PORT}"
INFERENCE_RELAY_DUMMY_KEY = "codemem-local-relay"
LOOPBACK_DIRECT_ENV = {
    "HTTP_PROXY": "",
    "HTTPS_PROXY": "",
    "ALL_PROXY": "",
    "http_proxy": "",
    "https_proxy": "",
    "all_proxy": "",
    "NO_PROXY": "127.0.0.1,localhost",
    "no_proxy": "127.0.0.1,localhost",
}


def gateway_hosts(urls: tuple[str, ...]) -> tuple[str, ...]:
    """Return normalized, unique HTTPS hostnames from model gateway URLs."""
    hosts: list[str] = []
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError(f"Model gateway must be an HTTPS URL: {url!r}")
        if parsed.username or parsed.password:
            raise ValueError(f"Model gateway must not contain credentials: {url!r}")
        if parsed.query or parsed.fragment:
            raise ValueError(
                f"Model gateway must not contain a query or fragment: {url!r}"
            )
        if parsed.port not in (None, 443):
            raise ValueError(f"Model gateway must use HTTPS port 443: {url!r}")
        host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        try:
            ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError(
                f"Model gateway must use an exact hostname, not an IP address: {url!r}"
            )
        if host not in hosts:
            hosts.append(host)
    if not hosts:
        raise ValueError("At least one model gateway URL is required")
    return tuple(hosts)


def local_relay_base_url(upstream_url: str) -> str:
    """Mirror an upstream base path on the loopback inference relay."""
    gateway_hosts((upstream_url,))
    parsed = urlsplit(upstream_url)
    path = parsed.path.rstrip("/")
    return urlunsplit(("http", f"{INFERENCE_RELAY_HOST}:{INFERENCE_RELAY_PORT}", path, "", ""))
