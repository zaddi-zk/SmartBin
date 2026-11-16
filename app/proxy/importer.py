"""Proxy line parsing (section 13).

Real proxy dumps mix several formats. Normalise them all to one httpx URL:

    host:port
    host:port:user:password
    user:password@host:port
    scheme://host:port
    scheme://user:password@host:port

Anything that cannot be a proxy is dropped, not guessed at.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_SCHEMES = frozenset({"http", "https", "socks4", "socks5", "socks5h"})


def _valid_host(host: str) -> bool:
    return bool(host) and " " not in host and len(host) <= 253


def _valid_port(port: str) -> bool:
    return port.isdigit() and 1 <= int(port) <= 65535


def parse_proxy_line(raw: str) -> str | None:
    """Return a canonical proxy URL, or ``None`` when the line is not one."""
    text = raw.strip()
    if not text or text.startswith("#"):
        return None

    if "://" in text:
        parsed = urlsplit(text)
        if parsed.scheme.lower() not in _SCHEMES:
            return None
        if not parsed.hostname or parsed.port is None:
            return None
        return text

    # user:password@host:port
    if "@" in text:
        creds, _, hostpart = text.rpartition("@")
        host, sep, port = hostpart.rpartition(":")
        if sep and _valid_host(host) and _valid_port(port) and ":" in creds:
            return f"http://{creds}@{host}:{port}"
        return None

    parts = text.split(":")
    if len(parts) == 2:
        host, port = parts
        if _valid_host(host) and _valid_port(port):
            return f"http://{host}:{port}"
        return None
    if len(parts) == 4:
        host, port, user, password = parts
        if _valid_host(host) and _valid_port(port) and user and password:
            return f"http://{user}:{password}@{host}:{port}"
    return None


def parse_proxy_text(text: str) -> list[str]:
    """Parse a pasted/loaded dump, preserving order and dropping duplicates."""
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        url = parse_proxy_line(line)
        if url is not None and url not in seen:
            seen.add(url)
            out.append(url)
    return out
