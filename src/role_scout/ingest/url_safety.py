"""SSRF-safe URL validation for the manual ingestion fetcher.

Resolves the hostname via DNS and rejects any address that targets
loopback, link-local, private RFC1918, multicast, or cloud-metadata ranges
before the HTTP request is made.  Also validates after each redirect.
"""
from __future__ import annotations

import ipaddress
import socket

from role_scout.compat.logging import get_logger

logger = get_logger(__name__)

# Cloud metadata address (AWS, GCP, Azure all use the same range).
_METADATA_IP = ipaddress.ip_address("169.254.169.254")

_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("10.0.0.0/8"),         # RFC1918 private
    ipaddress.ip_network("172.16.0.0/12"),      # RFC1918 private
    ipaddress.ip_network("192.168.0.0/16"),     # RFC1918 private
    ipaddress.ip_network("169.254.0.0/16"),     # link-local / cloud metadata
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local (fc::/7 covers fc:: and fd::)
    ipaddress.ip_network("224.0.0.0/4"),        # IPv4 multicast
    ipaddress.ip_network("ff00::/8"),           # IPv6 multicast
    ipaddress.ip_network("0.0.0.0/8"),          # "this" network
    ipaddress.ip_network("100.64.0.0/10"),      # shared address space (RFC6598 / CGN)
)


class SSRFBlockedError(ValueError):
    """Raised when a URL resolves to a blocked (private/loopback/metadata) address."""


def _is_blocked_address(addr: str) -> bool:
    """Return True if *addr* (dotted string or IPv6) falls in any blocked network."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        # Unparseable — treat as blocked for safety.
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def validate_fetchable_url(url: str) -> str:
    """Validate that *url* is safe to fetch (no SSRF vectors).

    Checks:
    1. Scheme must be ``https://`` (``http://`` is rejected).
    2. Hostname resolves via DNS without error.
    3. Every resolved IP is outside loopback, link-local, RFC1918, multicast,
       and cloud-metadata ranges.

    Returns the (unchanged) validated URL on success.

    Raises:
        SSRFBlockedError: If the URL fails any check.
    """
    stripped = url.strip()

    # 1. Scheme check — only https allowed.
    if not stripped.lower().startswith("https://"):
        raise SSRFBlockedError(f"Only https:// URLs are allowed, got: {stripped[:120]!r}")

    # 2. Extract hostname (strip scheme, path, port).
    remainder = stripped[len("https://"):]
    # Remove path/query/fragment
    host_port = remainder.split("/")[0].split("?")[0].split("#")[0]
    # Remove port
    # Handle IPv6 addresses like [::1]:8080
    if host_port.startswith("["):
        bracket_end = host_port.find("]")
        hostname = host_port[1:bracket_end] if bracket_end != -1 else host_port[1:]
    else:
        hostname = host_port.rsplit(":", 1)[0] if ":" in host_port else host_port

    if not hostname:
        raise SSRFBlockedError(f"Could not extract hostname from URL: {stripped[:120]!r}")

    # 3. Literal IP fast-path — check before DNS.
    try:
        literal_ip = ipaddress.ip_address(hostname)
        if _is_blocked_address(str(literal_ip)):
            raise SSRFBlockedError(f"URL resolves to a blocked address ({literal_ip}): {stripped[:120]!r}")
        return stripped
    except ValueError:
        pass  # Not a literal IP — continue to DNS resolution.

    # 4. DNS resolution and IP check.
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SSRFBlockedError(f"DNS resolution failed for {hostname!r}: {exc}") from exc

    for family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        if _is_blocked_address(ip_str):
            logger.warning(
                "ssrf_blocked",
                hostname=hostname,
                resolved_ip=ip_str,
                url=stripped[:80],
            )
            raise SSRFBlockedError(
                f"URL hostname {hostname!r} resolves to a blocked address ({ip_str}): {stripped[:120]!r}"
            )

    return stripped
