"""Fetching an address somebody typed: bounded always, and kept to the public internet when a user typed it.

A dashboard widget reads a feed, a calendar, or an API. An administrator's
widget may read anything the node can reach, which is the point of it: the
services of the administrator's own network. An address a user gave is
another matter, since the server would fetch it from inside that network, so
`fetch(..., public_only=True)` resolves the host itself, refuses any address
that is not a public one (loopback, private, link-local, shared, reserved,
multicast, and their IPv6 forms), and connects to the address it checked, so
a name that resolves differently a moment later changes nothing. Redirects
are followed by hand, each hop checked the same way.

Every fetch is `http` or `https`, carries no credential of the node, stops at
`max_bytes`, and gives up after `timeout` seconds.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

#: Redirects followed before giving up.
MAX_REDIRECTS = 4
#: Names the client and where to read about it, which sites that turn anonymous clients away ask for.
USER_AGENT = "Mozilla/5.0 (compatible; SealSkin-Dashboard; +https://github.com/selkies-project/sealskin)"

#: Set by a test to answer the fetches itself.
_transport: httpx.AsyncBaseTransport | None = None


class Refused(Exception):
    """The address may not be fetched, or what answered could not be taken."""


@dataclass
class Fetched:
    """What an address answered.

    Attributes:
        status: The HTTP status.
        content: The body, at most the `max_bytes` asked for.
        media_type: The `Content-Type` without its parameters, lower case.
        seconds: How long the answer took.
    """

    status: int
    content: bytes
    media_type: str
    seconds: float


def is_public(address: str) -> bool:
    """Tell whether an IP address is one of the public internet."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if mapped is not None:
            ip = mapped
    return ip.is_global and not ip.is_multicast


async def _resolve(host: str, port: int) -> list[str]:
    """Return the addresses a host name resolves to."""
    loop = asyncio.get_running_loop()
    try:
        found = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise Refused(f"{host} does not resolve.") from exc
    return [entry[4][0] for entry in found]


def split(url: str) -> tuple[str, str, int]:
    """Return the scheme, host, and port of an `http` or `https` address, or raise `Refused`."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise Refused("The address is not a web address.") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refused("The address is not an http or https address.")
    if parts.username or parts.password:
        raise Refused("The address carries a user name or a password.")
    return parts.scheme, parts.hostname, port or (443 if parts.scheme == "https" else 80)


async def _pinned(url: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """Return the address to connect to, with the headers and extensions that keep it the host asked for.

    Raises:
        Refused: When the host has an address that is not public.
    """
    scheme, host, port = split(url)
    addresses = await _resolve(host, port)
    if not addresses or not all(is_public(address) for address in addresses):
        raise Refused(f"{host} is not an address of the public internet.")
    parts = urlsplit(url)
    literal = f"[{addresses[0]}]" if ":" in addresses[0] else addresses[0]
    default = 443 if scheme == "https" else 80
    netloc = literal if port == default else f"{literal}:{port}"
    target = parts._replace(netloc=netloc).geturl()
    name = host if port == default else f"{host}:{port}"
    return target, {"Host": name}, {"sni_hostname": host}


async def fetch(
    url: str,
    *,
    public_only: bool,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    max_bytes: int = 2_000_000,
    timeout: float = 10.0,
) -> Fetched:
    """Fetch an address and return what it answered.

    Args:
        url: An `http` or `https` address.
        public_only: Refuse any host that is not of the public internet, at every hop.
        method: The HTTP method.
        headers: Request headers.
        body: A request body.
        max_bytes: Bytes of the answer read at most; a longer one is refused.
        timeout: Seconds the whole exchange may take.

    Raises:
        Refused: For an address that may not be fetched, a longer answer, too many redirects, or a failed exchange.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    sent = {"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})}
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=_transport) as client:
                for _ in range(MAX_REDIRECTS + 1):
                    split(url)
                    target, extra, extensions = (url, {}, {})
                    if public_only:
                        target, extra, extensions = await _pinned(url)
                    request = client.build_request(
                        method, target, headers={**sent, **extra}, content=body, extensions=extensions
                    )
                    response = await client.send(request, stream=True)
                    try:
                        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                            url = urljoin(url, response.headers["location"])
                            if response.status_code == 303:
                                method, body = "GET", None
                            continue
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > max_bytes:
                                raise Refused(f"The answer is longer than {max_bytes} bytes.")
                        media = response.headers.get("content-type", "").split(";")[0].strip().lower()
                        return Fetched(response.status_code, bytes(data), media, loop.time() - started)
                    finally:
                        await response.aclose()
                raise Refused("The address redirects too many times.")
    except TimeoutError as exc:
        raise Refused(f"No answer within {timeout:g} seconds.") from exc
    except httpx.HTTPError as exc:
        raise Refused(f"The address could not be fetched: {type(exc).__name__}.") from exc
