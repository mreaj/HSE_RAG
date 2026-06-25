"""
SSRF guard for web-source fetching.

Blocks requests to private / loopback / link-local / reserved IP ranges and
non-http(s) schemes, so an admin-entered URL (or an open redirect) can't be
used to reach internal services. Mirrors the protection in the original
project's security.py.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def is_safe_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"unparseable URL: {e}"

    if parsed.scheme not in ("http", "https"):
        return False, f"scheme '{parsed.scheme}' not allowed (use http/https)"
    if not parsed.hostname:
        return False, "missing hostname"

    host = parsed.hostname
    # Resolve all A/AAAA records and reject if ANY is private/reserved.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        return False, f"DNS resolution failed: {e}"

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False, f"resolves to blocked address {ip_str}"
    return True, ""
